#!/usr/bin/env python3
"""
영상 입력 VLM 분석 PoC 서버입니다.

구성 요약:
- FastAPI는 화면, runtime 상태 API, 영상 분석 job API를 제공합니다.
- 영상 분석은 요청 HTTP 연결 안에서 끝까지 처리하지 않고, 백그라운드 순차 worker가 처리합니다.
- 단일 RTX 4070 Ti PoC이므로 기본값은 병렬 분석이 아니라 순차 분석입니다.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import traceback
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from job_store import create_job, get_job, list_jobs, load_existing_jobs, update_job
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


BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
FRAME_DIR = TMP_DIR / "frames"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# PoC 안정성을 위한 기본 제한값입니다.
# 너무 큰 파일이나 너무 많은 프레임을 허용하면 RTX 4070 Ti 12GB 환경에서 vLLM 요청이 쉽게 실패할 수 있습니다.
MAX_SAMPLE_FRAMES = 12
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))
MAX_VIDEO_DURATION_SEC = int(os.environ.get("MAX_VIDEO_DURATION_SEC", "1800"))

FRAME_DIR.mkdir(parents=True, exist_ok=True)
load_existing_jobs(TMP_DIR)


app = FastAPI(title="Video VLM Analysis PoC")
app.mount("/frames", StaticFiles(directory=str(FRAME_DIR)), name="frames")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# 분석 작업은 큐에 넣고 한 개 worker가 순차 처리합니다.
# 단일 GPU에서 여러 VLM 요청을 동시에 보내면 VRAM 부족과 지연 원인을 구분하기 어려워지므로 PoC 기본값은 순차 처리입니다.
ANALYSIS_QUEUE: queue.Queue[str] = queue.Queue()
WORKER_STARTED = False
WORKER_LOCK = threading.Lock()


def build_vllm_payload(
    model_id: str,
    prompt: str,
    frame_data_urls: list[str],
    max_tokens: int,
) -> dict[str, Any]:
    """추출 프레임들을 vLLM OpenAI 호환 멀티이미지 요청 형식으로 변환합니다."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for data_url in frame_data_urls:
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    return {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }


def extract_answer(vllm_response: dict[str, Any]) -> str:
    """vLLM 응답에서 화면에 표시할 assistant 메시지를 추출합니다."""
    try:
        content = vllm_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def call_vllm(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """vLLM OpenAI 호환 API에 분석 요청을 보냅니다."""
    response = requests.post(endpoint, json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


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
    """분석 job을 큐에 넣고 worker가 없으면 시작합니다."""
    global WORKER_STARTED
    ANALYSIS_QUEUE.put(job_id)
    with WORKER_LOCK:
        if not WORKER_STARTED:
            worker = threading.Thread(target=analysis_worker, daemon=True)
            worker.start()
            WORKER_STARTED = True


def analysis_worker() -> None:
    """큐에 쌓인 영상 분석 작업을 하나씩 처리합니다."""
    while True:
        job_id = ANALYSIS_QUEUE.get()
        try:
            process_analysis_job(job_id)
        finally:
            ANALYSIS_QUEUE.task_done()


def process_analysis_job(job_id: str) -> None:
    """
    단일 영상 분석 job을 처리합니다.

    처리 단계:
    1. vLLM ready 확인
    2. 업로드 파일 또는 URL 영상 준비
    3. OpenCV로 균등 프레임 추출
    4. 프레임을 base64 data URL로 변환
    5. vLLM에 멀티이미지 분석 요청
    6. 결과와 원본 JSON을 job.json에 저장
    """
    job = get_job(job_id)
    if not job:
        return

    try:
        update_job(job_id, status="running", message="vLLM 상태를 확인하는 중입니다.")
        vllm_status = get_vllm_status()
        if not vllm_status.get("running"):
            raise RuntimeError(
                f"{vllm_status.get('message', 'vLLM 서버가 아직 준비되지 않았습니다.')} "
                "상단의 vLLM 시작 버튼을 누른 뒤 /v1/models 응답이 확인되면 다시 분석하세요."
            )

        source = job["source"]
        settings = job["settings"]
        job_dir = Path(job["job_dir"])

        update_job(job_id, message="영상 입력을 준비하는 중입니다.")
        if source["type"] == "upload":
            video_path = Path(source["path"])
        else:
            video_path = download_video(source["url"], job_dir)
            source["path"] = str(video_path)
            update_job(job_id, source=source)

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
        update_job(job_id, video_info=video_info, frames=frames, message=f"{len(frames)}개 프레임을 추출했습니다.")

        update_job(job_id, message="프레임을 vLLM 요청용 base64 이미지로 변환하는 중입니다.")
        frame_data_urls = [encode_frame_to_data_url(Path(frame["path"])) for frame in frames]
        payload = build_vllm_payload(
            str(settings["model_id"]),
            str(settings["prompt"]),
            frame_data_urls,
            int(settings["max_tokens"]),
        )

        update_job(job_id, message="vLLM에 영상 분석 요청을 보내는 중입니다.")
        raw_response = call_vllm(str(settings["endpoint"]), payload)
        update_job(
            job_id,
            status="done",
            message="분석이 완료되었습니다.",
            answer=extract_answer(raw_response),
            raw=raw_response,
        )
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        mark_job_failed(job_id, f"vLLM 요청 실패: {detail}", error)
    except Exception as error:
        mark_job_failed(job_id, classify_user_error(error), error)


def mark_job_failed(job_id: str, message: str, error: Exception) -> None:
    """실패 상태와 디버깅용 traceback을 job.json에 저장합니다."""
    update_job(
        job_id,
        status="failed",
        message=message,
        error={
            "message": message,
            "type": type(error).__name__,
            "traceback": traceback.format_exc(),
        },
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
) -> dict[str, Any]:
    """폼 입력을 분석 job으로 만들고 큐에 넣습니다."""
    validate_analysis_inputs(video_file, video_url, frame_count, max_tokens)
    source: dict[str, Any]
    settings = {
        "frame_count": frame_count,
        "max_tokens": max_tokens,
        "model_id": model_id,
        "endpoint": endpoint,
        "prompt": prompt,
    }

    if video_file and video_file.filename:
        source = {"type": "upload", "name": video_file.filename}
    else:
        source = {"type": "url", "url": video_url.strip(), "name": video_url.strip()}

    job = create_job(TMP_DIR, source=source, settings=settings)

    if video_file and video_file.filename:
        saved_path = await save_upload_file(video_file, Path(job["job_dir"]), max_bytes=MAX_UPLOAD_BYTES)
        source.update({"path": str(saved_path), "size_bytes": saved_path.stat().st_size})
        update_job(job["job_id"], source=source, message="업로드 파일을 저장했고 분석 대기열에 넣었습니다.")
    else:
        update_job(job["job_id"], message="영상 URL 분석 작업을 대기열에 넣었습니다.")

    enqueue_analysis_job(job["job_id"])
    return get_job(job["job_id"]) or job


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


@app.post("/api/jobs/video")
async def api_create_video_job(
    video_file: UploadFile | None = File(default=None),
    video_url: str = Form(default=""),
    frame_count: int = Form(default=DEFAULT_FRAME_COUNT),
    max_tokens: int = Form(default=512),
    model_id: str = Form(default=DEFAULT_MODEL_ID),
    endpoint: str = Form(default=DEFAULT_VLLM_ENDPOINT),
    prompt: str = Form(default="이 영상에서 발생한 주요 상황을 시간 순서대로 한국어로 요약해줘."),
) -> JSONResponse:
    """영상 분석 작업을 생성하고 즉시 job 상태를 반환합니다."""
    job = await create_video_job_from_form(video_file, video_url, frame_count, max_tokens, model_id, endpoint, prompt)
    return JSONResponse(job)


@app.get("/api/jobs")
def api_list_jobs(limit: int = 20) -> dict[str, Any]:
    """최근 영상 분석 작업 목록을 반환합니다."""
    return {"jobs": list_jobs(limit=limit)}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    """단일 영상 분석 작업 상태와 결과를 반환합니다."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="해당 job_id를 찾을 수 없습니다.")
    return job


@app.post("/api/analyze-video")
async def api_analyze_video_compat(
    video_file: UploadFile | None = File(default=None),
    video_url: str = Form(default=""),
    frame_count: int = Form(default=DEFAULT_FRAME_COUNT),
    max_tokens: int = Form(default=512),
    model_id: str = Form(default=DEFAULT_MODEL_ID),
    endpoint: str = Form(default=DEFAULT_VLLM_ENDPOINT),
    prompt: str = Form(default="이 영상에서 발생한 주요 상황을 시간 순서대로 한국어로 요약해줘."),
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
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_video_duration_sec": MAX_VIDEO_DURATION_SEC,
        "processing_mode": "sequential",
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
