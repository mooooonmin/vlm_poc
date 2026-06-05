#!/usr/bin/env python3
"""
영상 입력 VLM 분석 PoC 서버.

FastAPI는 API 라우팅과 정적 파일 제공만 담당합니다.
화면 구조, 스타일, 브라우저 로직은 templates/index.html 및 static 파일로 분리했습니다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from runtime_utils import (
    DEFAULT_MODEL_ID,
    DEFAULT_VLLM_ENDPOINT,
    collect_timeslicing_logs,
    get_gpu_status,
    get_timeslicing_summary,
    get_vllm_status,
    start_vllm_container,
    stop_vllm_container,
)
from video_utils import (
    DEFAULT_FRAME_COUNT,
    create_job_dir,
    download_video,
    encode_frame_to_data_url,
    save_upload_file,
    sample_video_frames,
)


# 임시 산출물은 tmp 폴더 아래에 저장하고, 추출 프레임은 브라우저 미리보기로 제공합니다.
BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
FRAME_DIR = TMP_DIR / "frames"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
FRAME_DIR.mkdir(parents=True, exist_ok=True)


app = FastAPI(title="Video VLM Analysis PoC")
app.mount("/frames", StaticFiles(directory=str(FRAME_DIR)), name="frames")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def build_vllm_payload(
    model_id: str,
    prompt: str,
    frame_data_urls: list[str],
    max_tokens: int,
) -> dict[str, Any]:
    """추출 프레임들을 vLLM OpenAI 호환 멀티 이미지 요청 형식으로 변환합니다."""
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


@app.get("/")
def index() -> FileResponse:
    """분리된 HTML 파일을 반환합니다."""
    return FileResponse(TEMPLATE_DIR / "index.html")


@app.get("/api/gpu-status")
def api_gpu_status() -> dict[str, Any]:
    """
    nvidia-smi 기반 CUDA/GPU 상태를 반환합니다.

    화면의 "GPU 상태" 확인 버튼에서 호출합니다.
    이 API가 실패하면 모델 분석 이전에 NVIDIA 드라이버, CUDA, GPU 인식 상태를 먼저 점검해야 합니다.
    """
    return get_gpu_status()


@app.get("/api/vllm-status")
def api_vllm_status() -> dict[str, Any]:
    """
    Docker 컨테이너 기준 vLLM 실행 상태를 반환합니다.

    단순히 컨테이너 존재 여부만 보는 것이 아니라 `/v1/models` 응답도 확인합니다.
    모델 로딩이 끝나기 전에는 컨테이너가 떠 있어도 running=false로 보일 수 있습니다.
    """
    return get_vllm_status()


@app.get("/api/timeslicing")
def api_timeslicing() -> dict[str, Any]:
    """
    time-slicing 적용 방향과 현재 초안 파일 정보를 반환합니다.

    로컬 RTX 4070 Ti 테스트에서는 time-slicing을 직접 적용하지 않습니다.
    이 API는 향후 Kubernetes GPU 노드에서 적용할 설정 파일 위치와 주의사항을 화면에 보여주기 위한 용도입니다.
    """
    return get_timeslicing_summary(BASE_DIR)


@app.post("/api/timeslicing/logs")
def api_collect_timeslicing_logs() -> dict[str, Any]:
    """
    Kubernetes time-slicing 검증에 필요한 로그를 파일로 수집합니다.

    이 API는 실제 time-slicing을 적용하지 않습니다.
    현재 환경에서 kubectl, NVIDIA device-plugin, node GPU 리소스가 어떤 상태인지 증거 파일을 남깁니다.
    """
    return collect_timeslicing_logs(BASE_DIR)


@app.post("/api/start-vllm")
def api_start_vllm() -> dict[str, Any]:
    """
    Docker 기반 vLLM 컨테이너를 시작합니다.

    사용자가 화면에서 `vLLM 시작`을 누르면 이 API가 호출됩니다.
    내부적으로는 `docker run --gpus all ... vllm/vllm-openai` 명령을 실행합니다.
    모델 다운로드와 로딩에는 시간이 걸릴 수 있으므로 시작 직후 바로 분석 요청을 보내지 말고 상태를 확인해야 합니다.
    """
    return start_vllm_container()


@app.post("/api/stop-vllm")
def api_stop_vllm() -> dict[str, Any]:
    """
    Docker 기반 vLLM 컨테이너를 중지합니다.

    GPU 메모리를 반환하거나 설정을 바꿔 다시 실행하고 싶을 때 사용합니다.
    컨테이너는 제거되지만 Hugging Face 모델 캐시는 호스트 폴더에 남도록 구성되어 있습니다.
    """
    return stop_vllm_container()


@app.post("/api/analyze-video")
async def api_analyze_video(
    video_file: UploadFile | None = File(default=None),
    video_url: str = Form(default=""),
    frame_count: int = Form(default=DEFAULT_FRAME_COUNT),
    max_tokens: int = Form(default=512),
    model_id: str = Form(default=DEFAULT_MODEL_ID),
    endpoint: str = Form(default=DEFAULT_VLLM_ENDPOINT),
    prompt: str = Form(default="이 영상에서 발생한 주요 상황을 시간 순서대로 한국어로 요약해줘."),
) -> JSONResponse:
    """영상 입력을 저장하고 프레임을 추출한 뒤 vLLM 분석 결과를 반환합니다."""
    if not video_file and not video_url.strip():
        raise HTTPException(status_code=400, detail="영상 파일 또는 영상 URL 중 하나는 필요합니다.")

    if frame_count < 1 or frame_count > 12:
        raise HTTPException(status_code=400, detail="샘플 프레임 수는 1~12 범위여야 합니다.")

    # 분석 요청 전에 vLLM 서버가 실제로 응답하는지 먼저 확인합니다.
    # vLLM이 떠 있지 않으면 영상 다운로드/프레임 추출을 진행해도 최종 분석이 불가능하므로,
    # 사용자에게 "영상 문제"가 아니라 "서빙 서버 문제"임을 명확히 알려줍니다.
    vllm_status = get_vllm_status()
    if not vllm_status.get("running"):
        raise HTTPException(
            status_code=503,
            detail=(
                f"{vllm_status.get('message', 'vLLM 서버가 실행 중이 아닙니다.')} "
                "화면 상단의 vLLM 시작 버튼을 누르고 /v1/models 응답이 확인된 뒤 다시 분석하세요."
            ),
        )

    job_dir = create_job_dir(TMP_DIR)
    try:
        # 파일 업로드와 URL이 모두 있으면 파일 업로드를 우선합니다.
        if video_file and video_file.filename:
            video_path = await save_upload_file(video_file, job_dir)
            source = {"type": "upload", "name": video_file.filename}
        else:
            video_path = download_video(video_url.strip(), job_dir)
            source = {"type": "url", "name": video_url.strip()}

        sample_result = sample_video_frames(video_path, FRAME_DIR, frame_count)
        frame_data_urls = [encode_frame_to_data_url(frame.path) for frame in sample_result.frames]
        payload = build_vllm_payload(model_id, prompt, frame_data_urls, max_tokens)
        raw_response = call_vllm(endpoint, payload)

        result = {
            "source": source,
            "video_info": {
                "fps": sample_result.fps,
                "frame_count": sample_result.total_frames,
                "duration_sec": sample_result.duration_sec,
                "sampled_frame_count": len(sample_result.frames),
            },
            "frames": [
                {
                    "index": frame.index,
                    "timestamp_sec": frame.timestamp_sec,
                    "preview_url": f"/frames/{frame.path.name}",
                }
                for frame in sample_result.frames
            ],
            "answer": extract_answer(raw_response),
            "raw": raw_response,
        }
        return JSONResponse(result)
    except requests.HTTPError as error:
        detail = error.response.text if error.response is not None else str(error)
        raise HTTPException(status_code=502, detail=f"vLLM 요청 실패: {detail}") from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


def main() -> None:
    """개발용 FastAPI 서버를 실행합니다."""
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8080"))
    uvicorn.run("app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
