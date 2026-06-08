#!/usr/bin/env python3
"""
영상 입력 VLM 분석 PoC 서버입니다.

구성 요약:
- FastAPI는 화면, runtime 상태 API, 영상 분석 job API를 제공합니다.
- 영상 분석은 요청 HTTP 연결 안에서 끝까지 처리하지 않고, dispatcher가 ready 상태 vLLM worker에 배정합니다.
- 단일 RTX 4070 Ti PoC의 기본값은 worker 1개이므로 기존처럼 순차 분석입니다.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from job_store import cleanup_finished_jobs, create_job, get_job, get_job_stats, list_batch_jobs, list_jobs, load_existing_jobs, update_job
from prompt_utils import (
    DEFAULT_USER_REQUEST,
    KOREAN_REPAIR_PROMPT,
    build_text_only_payload,
    build_vllm_payload,
    extract_answer,
    normalize_answer_text,
    refine_question_specific_answer,
)
from runtime_utils import (
    DEFAULT_GPU_MEMORY_UTILIZATION,
    DEFAULT_MAX_MODEL_LEN,
    DEFAULT_MODEL_ID,
    DEFAULT_VLLM_ENDPOINT,
    collect_timeslicing_logs,
    get_gpu_status,
    get_timeslicing_summary,
    get_vllm_logs,
    get_vllm_status,
    start_vllm_container,
    stop_vllm_container,
)
from video_utils import (
    DEFAULT_FRAME_COUNT,
    encode_frame_to_data_url,
    save_upload_file,
    sample_video_frames,
    download_video,
)
from worker_registry import acquire_ready_worker, list_workers, refresh_workers, release_worker


BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
FRAME_DIR = TMP_DIR / "frames"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# PoC 안정성을 위한 기본 제한값입니다.
# 너무 큰 파일이나 너무 많은 프레임을 허용하면 RTX 4070 Ti 12GB 환경에서 vLLM 요청이 쉽게 실패할 수 있습니다.
MAX_SAMPLE_FRAMES = 12
MAX_BATCH_VIDEOS = 3
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))
MAX_VIDEO_DURATION_SEC = int(os.environ.get("MAX_VIDEO_DURATION_SEC", "1800"))
KOREAN_RETRY_ENABLED = os.environ.get("KOREAN_RETRY_ENABLED", "1") != "0"
KOREAN_MIN_HANGUL = int(os.environ.get("KOREAN_MIN_HANGUL", "5"))
KOREAN_MIN_RATIO = float(os.environ.get("KOREAN_MIN_RATIO", "0.2"))

FRAME_DIR.mkdir(parents=True, exist_ok=True)
load_existing_jobs(TMP_DIR)


app = FastAPI(title="Video VLM Analysis PoC")
app.mount("/frames", StaticFiles(directory=str(FRAME_DIR)), name="frames")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# 분석 작업은 큐에 넣고 dispatcher가 사용 가능한 vLLM worker에 배정합니다.
# 여기서 worker는 Python thread가 아니라 "요청을 받을 수 있는 vLLM API 서버 1개"입니다.
# 로컬 RTX 4070 Ti 테스트에서는 기본 worker가 1개라 기존처럼 순차 처리됩니다.
# Kubernetes time-slicing 테스트에서는 VLLM_WORKERS 환경변수에 여러 endpoint를 넣어 여러 vLLM Pod/Service로 분산할 수 있습니다.
ANALYSIS_QUEUE: queue.Queue[str] = queue.Queue()
DISPATCHER_STARTED = False
DISPATCHER_LOCK = threading.Lock()


def call_vllm(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """vLLM OpenAI 호환 API에 분석 요청을 보냅니다."""
    response = requests.post(endpoint, json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


def assess_korean_response(answer: str) -> dict[str, Any]:
    """
    모델 응답이 한국어인지 간단히 점검합니다.

    완전한 언어 판별기는 아니지만, PoC 반복 테스트에서는 "한국어 답변이 아닌 결과"를 빠르게 찾아내는
    품질 신호로 충분합니다. 한글 글자 수와 전체 문자 대비 비율을 함께 기록합니다.
    """
    text = answer.strip()
    hangul_count = sum(1 for char in text if "가" <= char <= "힣")
    letter_count = sum(1 for char in text if char.isalpha())
    hangul_ratio = round(hangul_count / letter_count, 3) if letter_count else 0
    return {
        "ok": hangul_count >= KOREAN_MIN_HANGUL and hangul_ratio >= KOREAN_MIN_RATIO,
        "hangul_count": hangul_count,
        "letter_count": letter_count,
        "hangul_ratio": hangul_ratio,
        "min_hangul": KOREAN_MIN_HANGUL,
        "min_ratio": KOREAN_MIN_RATIO,
    }


def append_gpu_snapshot(job_id: str, stage: str) -> None:
    """
    job 처리 중 GPU 상태를 스냅샷으로 남깁니다.

    같은 영상 요청이라도 GPU 메모리 상태에 따라 OOM이나 지연이 달라질 수 있으므로,
    분석 시작/프레임 추출 후/vLLM 요청 전후/종료 시점의 `nvidia-smi` 결과를 job.json에 저장합니다.
    """
    job = get_job(job_id)
    if not job:
        return
    snapshots = list(job.get("gpu_snapshots") or [])
    snapshots.append(
        {
            "stage": stage,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "gpu": get_gpu_status(),
        }
    )
    update_job(job_id, gpu_snapshots=snapshots[-20:])


def validate_analysis_inputs(
    video_file: UploadFile | None,
    video_url: str,
    frame_count: int,
    max_tokens: int,
) -> None:
    """분석 요청값을 PoC 제한값 안으로 검증합니다."""
    has_upload = bool(video_file and video_file.filename)
    has_url = bool(video_url.strip())
    if not has_upload and not has_url:
        raise HTTPException(status_code=400, detail="영상 파일 또는 영상 URL 중 하나가 필요합니다.")
    if frame_count < 1 or frame_count > MAX_SAMPLE_FRAMES:
        raise HTTPException(status_code=400, detail=f"샘플 프레임 수는 1~{MAX_SAMPLE_FRAMES} 범위여야 합니다.")
    if max_tokens < 64 or max_tokens > 2048:
        raise HTTPException(status_code=400, detail="최대 토큰은 64~2048 범위여야 합니다.")


def enqueue_analysis_job(job_id: str) -> None:
    """분석 job을 큐에 넣고 dispatcher가 없으면 시작합니다."""
    global DISPATCHER_STARTED
    ANALYSIS_QUEUE.put(job_id)
    with DISPATCHER_LOCK:
        if not DISPATCHER_STARTED:
            dispatcher = threading.Thread(target=analysis_dispatcher, daemon=True)
            dispatcher.start()
            DISPATCHER_STARTED = True


def analysis_dispatcher() -> None:
    """
    큐에 쌓인 job을 사용 가능한 vLLM worker에 배정합니다.

    worker가 1개이면 순차 처리이고, worker가 여러 개이면 각 worker가 비는 즉시 다음 job을 배정합니다.
    time-slicing은 Kubernetes에서 GPU slot을 늘려 보이게 하는 설정일 뿐이므로,
    실제 요청 분산은 이 dispatcher가 어떤 vLLM endpoint로 보낼지 결정해야 동작합니다.
    """
    while True:
        job_id = ANALYSIS_QUEUE.get()
        try:
            dispatch_analysis_job(job_id)
        finally:
            ANALYSIS_QUEUE.task_done()


def dispatch_analysis_job(job_id: str) -> None:
    """ready worker가 생길 때까지 기다린 뒤 job 처리 thread를 시작합니다."""
    while True:
        refresh_workers()
        worker = acquire_ready_worker(job_id)
        if worker:
            thread = threading.Thread(target=run_job_on_worker, args=(job_id, worker), daemon=True)
            thread.start()
            return

        update_job(
            job_id,
            status="queued",
            message="사용 가능한 vLLM worker를 기다리는 중입니다. vLLM이 아직 로딩 중이거나 모든 worker가 처리 중입니다.",
            worker_status=list_workers(),
        )
        time.sleep(3)


def run_job_on_worker(job_id: str, worker: dict[str, Any]) -> None:
    """배정된 worker에서 job을 처리하고, 끝나면 worker 점유를 해제합니다."""
    success = False
    error_message = ""
    try:
        process_analysis_job(job_id, worker)
        finished_job = get_job(job_id)
        success = bool(finished_job and finished_job.get("status") == "done")
        if not success:
            error_message = str((finished_job or {}).get("message", "job이 실패했습니다."))
    except Exception as error:
        error_message = str(error)
        mark_job_failed(job_id, classify_user_error(error), error)
    finally:
        release_worker(str(worker["worker_id"]), success=success, error_message=error_message)


def process_analysis_job(job_id: str, worker: dict[str, Any]) -> None:
    """
    단일 영상 분석 job을 처리합니다.

    처리 단계:
    1. 배정된 vLLM worker 기록
    2. 업로드 파일 또는 URL 영상 준비
    3. OpenCV로 균등 프레임 추출
    4. 프레임을 base64 data URL로 변환
    5. vLLM에 멀티이미지 분석 요청
    6. 결과와 원본 JSON을 job.json에 저장
    """
    job = get_job(job_id)
    if not job:
        return

    job_started_perf = time.perf_counter()
    current_stage = "startup"
    try:
        source = job["source"]
        settings = job["settings"]
        job_dir = Path(job["job_dir"])
        worker_endpoint = str(worker["endpoint"])
        append_gpu_snapshot(job_id, "job_start")

        # worker_id/worker_endpoint를 job.json에 남겨야 여러 영상 요청을 테스트할 때
        # 어떤 vLLM 서버가 어떤 요청을 처리했는지 성공/실패 원인을 추적할 수 있습니다.
        update_job(
            job_id,
            status="running",
            message=f"{worker['worker_id']}에 배정되어 영상 분석을 시작합니다.",
            worker_id=worker["worker_id"],
            worker_endpoint=worker_endpoint,
            started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            loop_checks={
                "1_korean_response": "pending",
                "2_real_video_stats": "pending",
                "3_gpu_snapshot": "running",
                "4_worker_assignment": "done",
            },
        )

        current_stage = "input_prepare"
        update_job(job_id, message="영상 입력을 준비하는 중입니다.")
        if source["type"] == "upload":
            video_path = Path(source["path"])
        else:
            current_stage = "video_download"
            video_path = download_video(source["url"], job_dir)
            source["path"] = str(video_path)
            update_job(job_id, source=source)

        current_stage = "frame_extract"
        frame_extract_started_perf = time.perf_counter()
        update_job(job_id, frame_extract_started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        update_job(job_id, message="OpenCV로 영상 메타데이터와 샘플 프레임을 추출하는 중입니다.")
        sample_result = sample_video_frames(video_path, FRAME_DIR, int(settings["frame_count"]))
        if sample_result.duration_sec > MAX_VIDEO_DURATION_SEC:
            raise RuntimeError(
                f"영상 길이가 PoC 제한을 초과했습니다. "
                f"현재 {sample_result.duration_sec:.1f}초, 제한 {MAX_VIDEO_DURATION_SEC}초입니다."
            )

        frames = [
            {
                "index": frame.index,
                "timestamp_sec": frame.timestamp_sec,
                "preview_url": f"/frames/{frame.path.name}",
                "path": str(frame.path),
            }
            for frame in sample_result.frames
        ]
        video_info = {
            "fps": sample_result.fps,
            "frame_count": sample_result.total_frames,
            "duration_sec": sample_result.duration_sec,
            "sampled_frame_count": len(sample_result.frames),
        }
        frame_extract_duration_ms = elapsed_ms(frame_extract_started_perf)
        update_job(
            job_id,
            video_info=video_info,
            frames=frames,
            frame_extract_finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            frame_extract_duration_ms=frame_extract_duration_ms,
            message=f"{len(frames)}개 프레임을 추출했습니다. 프레임 추출 시간: {frame_extract_duration_ms}ms",
        )
        append_gpu_snapshot(job_id, "frame_extract_finished")

        current_stage = "payload_prepare"
        update_job(job_id, message="프레임을 vLLM 요청용 base64 이미지로 변환하는 중입니다.")
        sampled_frames = [
            {
                "index": frame["index"],
                "timestamp_sec": frame["timestamp_sec"],
                "data_url": encode_frame_to_data_url(Path(frame["path"])),
            }
            for frame in frames
        ]
        payload = build_vllm_payload(
            str(settings["model_id"]),
            str(settings["prompt"]),
            sampled_frames,
            int(settings["max_tokens"]),
        )

        current_stage = "vllm_request"
        vllm_request_started_perf = time.perf_counter()
        append_gpu_snapshot(job_id, "vllm_request_start")
        update_job(
            job_id,
            vllm_request_started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            message=f"{worker['worker_id']} vLLM endpoint로 영상 분석 요청을 보내는 중입니다.",
        )
        raw_response = call_vllm(worker_endpoint, payload)
        answer = normalize_answer_text(extract_answer(raw_response))
        answer = refine_question_specific_answer(answer, str(settings["prompt"]))
        korean_check = assess_korean_response(answer)
        korean_retry_used = False
        korean_repair_used = False
        korean_fallback_used = False

        # 한국어 응답 보정 흐름입니다.
        # 1차 vLLM 응답이 한국어 기준을 통과하면 그대로 저장합니다.
        # 기준을 통과하지 못하면 같은 이미지 프레임과 질문으로 한국어 강제 프롬프트를 붙여 1회 재요청합니다.
        # 재요청도 실패하면 "영상 재분석"이 아니라 "이미 받은 텍스트 응답을 한국어로 정리"하는 텍스트-only 요청을 보냅니다.
        # 마지막까지 실패하면 사용자가 실패 원인을 볼 수 있도록 한국어 경고문과 원문 응답을 함께 표시합니다.
        if KOREAN_RETRY_ENABLED and not korean_check["ok"]:
            update_job(
                job_id,
                message="응답이 한국어 기준을 통과하지 못해 한국어 강제 프롬프트로 1회 재요청합니다.",
                korean_check=korean_check,
            )
            retry_payload = build_vllm_payload(
                str(settings["model_id"]),
                str(settings["prompt"]),
                sampled_frames,
                int(settings["max_tokens"]),
                strict_korean=True,
            )
            raw_response = call_vllm(worker_endpoint, retry_payload)
            answer = normalize_answer_text(extract_answer(raw_response))
            answer = refine_question_specific_answer(answer, str(settings["prompt"]))
            korean_check = assess_korean_response(answer)
            korean_retry_used = True
        if KOREAN_RETRY_ENABLED and not korean_check["ok"] and answer.strip():
            update_job(
                job_id,
                message="재요청 응답도 한국어 기준을 통과하지 못해 원문 응답을 한국어로 정리합니다.",
                korean_check=korean_check,
                korean_retry_used=korean_retry_used,
            )
            repair_payload = build_text_only_payload(
                str(settings["model_id"]),
                KOREAN_REPAIR_PROMPT.format(answer=answer.strip()),
                int(settings["max_tokens"]),
            )
            repair_response = call_vllm(worker_endpoint, repair_payload)
            repair_answer = normalize_answer_text(extract_answer(repair_response))
            repair_answer = refine_question_specific_answer(repair_answer, str(settings["prompt"]))
            repair_check = assess_korean_response(repair_answer)
            if repair_answer.strip():
                raw_response = {
                    "original_multimodal_response": raw_response,
                    "korean_repair_response": repair_response,
                }
                answer = repair_answer
                korean_check = repair_check
                korean_repair_used = True
        if not korean_check["ok"]:
            answer = (
                "모델이 한국어 응답 지시를 따르지 않았습니다. "
                f"원문 응답은 다음과 같습니다: {answer.strip() or '(빈 응답)'}"
            )
            korean_check = assess_korean_response(answer)
            korean_fallback_used = True
        vllm_duration_ms = elapsed_ms(vllm_request_started_perf)
        append_gpu_snapshot(job_id, "vllm_request_finished")
        loop_checks = {
            "1_korean_response": "done" if korean_check["ok"] else "failed",
            "2_real_video_stats": "done",
            "3_gpu_snapshot": "done",
            "4_worker_assignment": "done",
        }

        # 화면에는 정리된 answer만 보여주고, 원본 vLLM JSON은 job.json의 raw 필드에 저장합니다.
        # 이렇게 해야 화면은 단순하게 유지하면서도, 품질 문제가 생겼을 때 원본 응답을 파일에서 추적할 수 있습니다.
        update_job(
            job_id,
            status="done",
            message=f"분석이 완료되었습니다. vLLM 요청 시간: {vllm_duration_ms}ms",
            answer=answer,
            raw=raw_response,
            korean_check=korean_check,
            korean_retry_used=korean_retry_used,
            korean_repair_used=korean_repair_used,
            korean_fallback_used=korean_fallback_used,
            loop_checks=loop_checks,
            vllm_request_finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            vllm_duration_ms=vllm_duration_ms,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            duration_ms=elapsed_ms(job_started_perf),
        )
        append_gpu_snapshot(job_id, "job_finished")
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        append_gpu_snapshot(job_id, "job_failed")
        mark_job_failed(job_id, f"vLLM 요청 실패: {detail}", error, current_stage, job_started_perf)
    except Exception as error:
        append_gpu_snapshot(job_id, "job_failed")
        mark_job_failed(job_id, classify_user_error(error), error, current_stage, job_started_perf)


def elapsed_ms(start_perf: float) -> int:
    """time.perf_counter 기준 경과 시간을 ms 정수로 반환합니다."""
    return int((time.perf_counter() - start_perf) * 1000)


def mark_job_failed(
    job_id: str,
    message: str,
    error: Exception,
    failure_stage: str = "unknown",
    job_started_perf: float | None = None,
) -> None:
    """실패 상태와 디버깅용 traceback을 job.json에 저장합니다."""
    changes: dict[str, Any] = {
        "status": "failed",
        "message": message,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "failure_stage": failure_stage,
        "failure_reason": type(error).__name__,
        "loop_checks": {
            "1_korean_response": "skipped" if failure_stage != "vllm_request" else "failed",
            "2_real_video_stats": "failed" if failure_stage in {"input_prepare", "video_download", "frame_extract"} else "done",
            "3_gpu_snapshot": "done",
            "4_worker_assignment": "done",
        },
        "error": {
            "message": message,
            "type": type(error).__name__,
            "traceback": traceback.format_exc(),
        },
    }
    if job_started_perf is not None:
        changes["duration_ms"] = elapsed_ms(job_started_perf)
    if failure_stage == "vllm_request":
        changes["vllm_request_finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if failure_stage == "frame_extract":
        changes["frame_extract_finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    update_job(
        job_id,
        **changes,
    )


def classify_user_error(error: Exception) -> str:
    """자주 만나는 실패 원인을 사용자가 이해하기 쉬운 문장으로 분류합니다."""
    text = str(error)
    lowered = text.lower()
    if "yt-dlp" in lowered:
        return f"YouTube/플랫폼 영상 다운로드에 실패했습니다. 비공개, 연령 제한, 지역 제한, 네트워크 차단 여부를 확인하세요. 원문: {text}"
    if "opencv" in lowered or "열 수 없습니다" in text or "frame" in lowered:
        return f"OpenCV가 영상을 열거나 프레임을 읽지 못했습니다. 파일 형식 또는 다운로드 결과를 확인하세요. 원문: {text}"
    if "vllm" in lowered or "/v1/models" in lowered:
        return f"vLLM 서버가 아직 준비되지 않았거나 응답하지 않습니다. 상단 상태와 vLLM 로그를 확인하세요. 원문: {text}"
    return text


async def create_video_job_from_form(
    video_file: UploadFile | None,
    video_url: str,
    frame_count: int,
    max_tokens: int,
    model_id: str,
    endpoint: str,
    prompt: str,
    batch_id: str | None = None,
    batch_index: int | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """폼 입력을 분석 job으로 만들고 큐에 넣습니다."""
    validate_analysis_inputs(video_file, video_url, frame_count, max_tokens)
    source: dict[str, Any]
    settings = {
        "frame_count": frame_count,
        "max_tokens": max_tokens,
        "model_id": model_id,
        # 단일 endpoint 입력값은 호환을 위해 저장하지만, 실제 요청은 dispatcher가 배정한 worker_endpoint로 보냅니다.
        # VLLM_WORKERS가 미설정이면 worker endpoint가 DEFAULT_VLLM_ENDPOINT 1개라 기존 동작과 같습니다.
        "endpoint": endpoint,
        "prompt": prompt,
    }

    if video_file and video_file.filename:
        source = {"type": "upload", "name": video_file.filename}
    else:
        source = {"type": "url", "url": video_url.strip(), "name": video_url.strip()}

    job = create_job(
        TMP_DIR,
        source=source,
        settings=settings,
        batch_id=batch_id,
        batch_index=batch_index,
        batch_size=batch_size,
    )
    update_job(job["job_id"], queued_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    if video_file and video_file.filename:
        saved_path = await save_upload_file(video_file, Path(job["job_dir"]), max_bytes=MAX_UPLOAD_BYTES)
        source.update({"path": str(saved_path), "size_bytes": saved_path.stat().st_size})
        update_job(job["job_id"], source=source, message="업로드 파일을 저장했고 분석 대기열에 넣었습니다.")
    else:
        update_job(job["job_id"], message="영상 URL 분석 작업을 대기열에 넣었습니다.")

    enqueue_analysis_job(job["job_id"])
    return get_job(job["job_id"]) or job


def _has_video_input(video_file: UploadFile | None, video_url: str) -> bool:
    """파일 또는 URL 중 하나라도 입력됐는지 확인합니다."""
    return bool(video_file and video_file.filename) or bool(video_url.strip())


def summarize_batch(batch_id: str) -> dict[str, Any]:
    """batch_id에 속한 job들의 진행률을 화면/API용으로 요약합니다."""
    jobs = list_batch_jobs(batch_id)
    status_counts = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for job in jobs:
        status = str(job.get("status", ""))
        if status in status_counts:
            status_counts[status] += 1

    total = len(jobs)
    finished = status_counts["done"] + status_counts["failed"]

    # batch 상태는 "모든 job이 끝났는지"와 "실패가 섞였는지"를 기준으로 계산합니다.
    # 개별 job은 성공/실패가 분리되어 있으므로, batch는 화면에서 전체 진행률을 보여주는 묶음 상태입니다.
    if total == 0:
        overall_status = "missing"
    elif status_counts["failed"] > 0 and finished == total:
        overall_status = "failed"
    elif status_counts["failed"] > 0:
        overall_status = "partial"
    elif status_counts["done"] == total:
        overall_status = "done"
    elif status_counts["running"] > 0:
        overall_status = "running"
    else:
        overall_status = "queued"

    return {
        "batch_id": batch_id,
        "status": overall_status,
        "total": total,
        "finished": finished,
        "status_counts": status_counts,
        "jobs": jobs,
    }


async def create_video_batch_from_form(
    items: list[tuple[UploadFile | None, str]],
    frame_count: int,
    max_tokens: int,
    model_id: str,
    endpoint: str,
    prompt: str,
) -> dict[str, Any]:
    """최대 3개 영상 입력을 각각 독립 job으로 만들고 하나의 batch_id로 묶습니다."""
    # 빈 슬롯은 무시합니다.
    # 화면은 최대 3개 입력 칸을 항상 보여주지만, 사용자가 실제로 파일 또는 URL을 넣은 슬롯만 job으로 생성합니다.
    active_items = [(video_file, video_url) for video_file, video_url in items if _has_video_input(video_file, video_url)]
    if not active_items:
        raise HTTPException(status_code=400, detail="최소 1개 이상의 영상 파일 또는 영상 URL이 필요합니다.")
    if len(active_items) > MAX_BATCH_VIDEOS:
        raise HTTPException(status_code=400, detail=f"batch 입력은 최대 {MAX_BATCH_VIDEOS}개까지만 지원합니다.")

    batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    created_jobs = []
    batch_size = len(active_items)

    # 각 영상은 서로 독립된 job_id를 가집니다.
    # batch_id는 화면에서 여러 job을 한 묶음으로 polling하고 비교하기 위한 그룹 ID입니다.
    for index, (video_file, video_url) in enumerate(active_items, start=1):
        job = await create_video_job_from_form(
            video_file,
            video_url,
            frame_count,
            max_tokens,
            model_id,
            endpoint,
            prompt,
            batch_id=batch_id,
            batch_index=index,
            batch_size=batch_size,
        )
        created_jobs.append(job)

    summary = summarize_batch(batch_id)
    summary["created_jobs"] = created_jobs
    return summary


# 화면과 런타임 상태 API입니다.
# GPU/vLLM/worker/time-slicing 상태는 분석 요청 전에 환경이 준비됐는지 확인하는 용도입니다.
@app.get("/")
def index() -> FileResponse:
    """분리된 HTML 파일을 반환합니다."""
    return FileResponse(TEMPLATE_DIR / "index.html")


@app.get("/api/gpu-status")
def api_gpu_status() -> dict[str, Any]:
    """nvidia-smi 기반 CUDA/GPU 상태를 반환합니다."""
    return get_gpu_status()


@app.get("/api/vllm-status")
def api_vllm_status() -> dict[str, Any]:
    """Docker 컨테이너와 /v1/models 기준 vLLM 상태를 반환합니다."""
    return get_vllm_status()


@app.get("/api/vllm/logs")
def api_vllm_logs(lines: int = 120) -> dict[str, Any]:
    """vLLM 컨테이너 로그 tail을 반환합니다."""
    return get_vllm_logs(lines=lines)


@app.get("/api/workers")
def api_workers() -> dict[str, Any]:
    """현재 등록된 vLLM worker 목록과 배정 상태를 반환합니다."""
    return {"workers": list_workers()}


@app.post("/api/workers/refresh")
def api_refresh_workers() -> dict[str, Any]:
    """각 vLLM worker의 `/v1/models` 응답을 확인해 ready/error 상태를 갱신합니다."""
    return {"workers": refresh_workers()}


@app.get("/api/timeslicing")
def api_timeslicing() -> dict[str, Any]:
    """time-slicing 적용 방향과 현재 초안 파일 정보를 반환합니다."""
    return get_timeslicing_summary(BASE_DIR)


@app.post("/api/timeslicing/logs")
def api_collect_timeslicing_logs() -> dict[str, Any]:
    """Kubernetes time-slicing 검증에 필요한 로그를 파일로 수집합니다."""
    return collect_timeslicing_logs(BASE_DIR)


@app.post("/api/start-vllm")
def api_start_vllm() -> dict[str, Any]:
    """Docker 기반 vLLM 컨테이너를 백그라운드로 시작합니다."""
    return start_vllm_container()


@app.post("/api/stop-vllm")
def api_stop_vllm() -> dict[str, Any]:
    """Docker 기반 vLLM 컨테이너를 중지하고 GPU 점유를 해제합니다."""
    return stop_vllm_container()


# 영상 분석 job 생성 API입니다.
# `/api/jobs/video-batch`가 현재 화면의 기본 경로이고, `/api/jobs/video`는 단일 영상 요청용입니다.
@app.post("/api/jobs/video")
async def api_create_video_job(
    video_file: UploadFile | None = File(default=None),
    video_url: str = Form(default=""),
    frame_count: int = Form(default=DEFAULT_FRAME_COUNT),
    max_tokens: int = Form(default=512),
    model_id: str = Form(default=DEFAULT_MODEL_ID),
    endpoint: str = Form(default=DEFAULT_VLLM_ENDPOINT),
    prompt: str = Form(default=DEFAULT_USER_REQUEST),
) -> JSONResponse:
    """영상 분석 작업을 생성하고 즉시 job 상태를 반환합니다."""
    job = await create_video_job_from_form(video_file, video_url, frame_count, max_tokens, model_id, endpoint, prompt)
    return JSONResponse(job)


@app.post("/api/jobs/video-batch")
async def api_create_video_batch(
    video_file_1: UploadFile | None = File(default=None),
    video_url_1: str = Form(default=""),
    video_file_2: UploadFile | None = File(default=None),
    video_url_2: str = Form(default=""),
    video_file_3: UploadFile | None = File(default=None),
    video_url_3: str = Form(default=""),
    frame_count: int = Form(default=DEFAULT_FRAME_COUNT),
    max_tokens: int = Form(default=512),
    model_id: str = Form(default=DEFAULT_MODEL_ID),
    endpoint: str = Form(default=DEFAULT_VLLM_ENDPOINT),
    prompt: str = Form(default=DEFAULT_USER_REQUEST),
) -> JSONResponse:
    """최대 3개 영상 분석 작업을 생성하고 하나의 batch 상태로 반환합니다."""
    batch = await create_video_batch_from_form(
        [
            (video_file_1, video_url_1),
            (video_file_2, video_url_2),
            (video_file_3, video_url_3),
        ],
        frame_count,
        max_tokens,
        model_id,
        endpoint,
        prompt,
    )
    return JSONResponse(batch)


# 분석 결과 조회 API입니다.
# 화면은 job_id/batch_id를 받아 polling하면서 queued -> running -> done/failed 변화를 갱신합니다.
@app.get("/api/jobs")
def api_list_jobs(limit: int = 20) -> dict[str, Any]:
    """최근 영상 분석 작업 목록을 반환합니다."""
    return {"jobs": list_jobs(limit=limit)}


@app.get("/api/batches/{batch_id}")
def api_get_batch(batch_id: str) -> dict[str, Any]:
    """batch_id로 묶인 최대 3개 영상 job의 진행률과 개별 결과를 반환합니다."""
    batch = summarize_batch(batch_id)
    if batch["total"] == 0:
        raise HTTPException(status_code=404, detail="해당 batch_id를 찾을 수 없습니다.")
    return batch


@app.get("/api/jobs/stats")
def api_job_stats(limit: int = 50) -> dict[str, Any]:
    """최근 영상 분석 작업의 성공/실패/처리시간/worker별 요약을 반환합니다."""
    return get_job_stats(limit=limit)


@app.post("/api/tmp/cleanup")
def api_cleanup_tmp_files(dry_run: bool = False) -> dict[str, Any]:
    """
    완료/실패한 분석 job의 임시 파일을 정리합니다.

    queued/running job은 삭제하지 않습니다.
    dry_run=true를 붙이면 실제 삭제 없이 삭제 예정 개수와 용량만 계산합니다.
    """
    return cleanup_finished_jobs(TMP_DIR, FRAME_DIR, dry_run=dry_run)


@app.get("/api/evaluations")
def api_evaluations(limit: int = 10) -> dict[str, Any]:
    """최근 evaluation run 리포트 목록을 반환합니다."""
    evaluations_root = BASE_DIR / "logs" / "evaluation"
    if not evaluations_root.exists():
        return {"evaluations": []}
    reports = []
    for summary_path in sorted(evaluations_root.glob("*/summary.json"), reverse=True):
        try:
            report = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report["summary_json_path"] = str(summary_path)
        report["summary_md_path"] = str(summary_path.with_name("summary.md"))
        reports.append(report)
        if len(reports) >= limit:
            break
    return {"evaluations": reports}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    """단일 영상 분석 작업 상태와 결과를 반환합니다."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="해당 job_id를 찾을 수 없습니다.")
    return job


# 오래된 클라이언트 호환 API입니다.
# 새 화면은 job 기반 비동기 흐름을 사용하지만, 기존 `/api/analyze-video` 호출이 바로 깨지지 않도록 남겨 둡니다.
@app.post("/api/analyze-video")
async def api_analyze_video_compat(
    video_file: UploadFile | None = File(default=None),
    video_url: str = Form(default=""),
    frame_count: int = Form(default=DEFAULT_FRAME_COUNT),
    max_tokens: int = Form(default=512),
    model_id: str = Form(default=DEFAULT_MODEL_ID),
    endpoint: str = Form(default=DEFAULT_VLLM_ENDPOINT),
    prompt: str = Form(default=DEFAULT_USER_REQUEST),
) -> JSONResponse:
    """
    기존 클라이언트 호환용 API입니다.

    이전에는 최종 분석 결과를 바로 반환했지만, 지금은 job_id와 현재 상태를 반환합니다.
    최신 화면은 `/api/jobs/video`를 직접 사용합니다.
    """
    job = await create_video_job_from_form(video_file, video_url, frame_count, max_tokens, model_id, endpoint, prompt)
    return JSONResponse(job)


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    """화면에 표시할 PoC 기본 설정값을 반환합니다."""
    return {
        "default_model_id": DEFAULT_MODEL_ID,
        "default_endpoint": DEFAULT_VLLM_ENDPOINT,
        "gpu_memory_utilization": DEFAULT_GPU_MEMORY_UTILIZATION,
        "max_model_len": DEFAULT_MAX_MODEL_LEN,
        "hf_token_configured": bool(os.environ.get("HF_TOKEN")),
        "max_sample_frames": MAX_SAMPLE_FRAMES,
        "max_batch_videos": MAX_BATCH_VIDEOS,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_video_duration_sec": MAX_VIDEO_DURATION_SEC,
        "processing_mode": "single-worker" if len(list_workers()) == 1 else "multi-worker-dispatch",
        "workers": list_workers(),
        "korean_retry_enabled": KOREAN_RETRY_ENABLED,
        "korean_min_hangul": KOREAN_MIN_HANGUL,
        "korean_min_ratio": KOREAN_MIN_RATIO,
    }


def main() -> None:
    """개발용 FastAPI 서버를 실행합니다."""
    host = os.environ.get("APP_HOST", "127.0.0.1")
    requested_port = int(os.environ.get("APP_PORT", "8080"))
    port = find_available_port(host, requested_port)
    if port != requested_port:
        print(f"요청한 포트 {requested_port}는 이미 사용 중입니다. 대신 {port} 포트로 실행합니다.")
    print(f"영상 VLM 분석 PoC 화면: http://{host}:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=False)


def find_available_port(host: str, start_port: int) -> int:
    """지정한 포트가 사용 중이면 다음 빈 포트를 찾아 반환합니다."""
    for port in range(start_port, start_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"사용 가능한 포트를 찾지 못했습니다: {start_port}~{start_port + 19}")


if __name__ == "__main__":
    main()
