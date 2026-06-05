#!/usr/bin/env python3
"""
영상 입력 VLM 분석 PoC 화면.

이 앱은 FastAPI 기반의 임시 테스트 화면입니다.
영상 파일 업로드 또는 영상 URL을 받아 프레임을 균등 추출한 뒤,
추출 프레임을 vLLM OpenAI 호환 API에 멀티 이미지 입력으로 전달합니다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from runtime_utils import (
    DEFAULT_CONTAINER_NAME,
    DEFAULT_GPU_MEMORY_UTILIZATION,
    DEFAULT_MAX_MODEL_LEN,
    DEFAULT_MODEL_ID,
    DEFAULT_VLLM_ENDPOINT,
    get_gpu_status,
    get_timeslicing_summary,
    collect_timeslicing_logs,
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
FRAME_DIR.mkdir(parents=True, exist_ok=True)


app = FastAPI(title="Video VLM Analysis PoC")
app.mount("/frames", StaticFiles(directory=str(FRAME_DIR)), name="frames")


INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>영상 VLM 분석 PoC</title>
  <style>
    :root {
      color-scheme: light;
      font-family: Arial, "Malgun Gothic", sans-serif;
      background: #f5f7fb;
      color: #1f2937;
    }
    body {
      margin: 0;
      padding: 24px;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 25px;
    }
    h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    p {
      line-height: 1.5;
    }
    .summary {
      margin: 0 0 18px;
      color: #4b5563;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 420px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    section {
      background: #fff;
      border: 1px solid #d9e1ec;
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
    }
    label {
      display: block;
      margin-bottom: 6px;
      font-weight: 700;
    }
    input, textarea {
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #c8d2df;
      border-radius: 6px;
      padding: 10px;
      font: inherit;
      background: #fff;
    }
    textarea {
      min-height: 150px;
      resize: vertical;
    }
    button {
      border: 0;
      border-radius: 6px;
      background: #155eef;
      color: #fff;
      font-weight: 700;
      padding: 10px 13px;
      cursor: pointer;
    }
    button.secondary {
      background: #374151;
    }
    button.danger {
      background: #b42318;
    }
    button:disabled {
      background: #9db5ed;
      cursor: wait;
    }
    .field {
      margin-bottom: 14px;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .button-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .status {
      min-height: 22px;
      margin-top: 10px;
      color: #4b5563;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
      gap: 10px;
    }
    .frame-card {
      border: 1px solid #d9e1ec;
      border-radius: 6px;
      background: #f9fafb;
      overflow: hidden;
    }
    .frame-card img {
      width: 100%;
      height: 96px;
      object-fit: cover;
      display: block;
    }
    .frame-card div {
      padding: 7px;
      font-size: 12px;
      color: #4b5563;
    }
    pre {
      margin: 0;
      padding: 12px;
      border-radius: 6px;
      background: #111827;
      color: #e5e7eb;
      white-space: pre-wrap;
      word-break: break-word;
      overflow: auto;
      min-height: 160px;
    }
    details {
      margin-top: 10px;
    }
    .hint {
      font-size: 13px;
      color: #6b7280;
    }
    @media (max-width: 900px) {
      body {
        padding: 14px;
      }
      .grid, .row {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <h1>영상 VLM 분석 PoC</h1>
    <p class="summary">
      영상 파일 또는 영상 URL을 입력하면 프레임을 균등 추출하고,
      Qwen VLM을 vLLM OpenAI 호환 API로 호출해 한국어 분석 결과를 받습니다.
    </p>

    <section>
      <h2>런타임 상태</h2>
      <div class="button-row">
        <button type="button" onclick="refreshRuntime()">상태 새로고침</button>
        <button type="button" onclick="startVllm()">vLLM 시작 / GPU 점유</button>
        <button type="button" class="danger" onclick="stopVllm()">vLLM 종료 / GPU 해제</button>
        <button type="button" class="secondary" onclick="collectTimeslicingLogs()">Time-slicing 로그 수집</button>
      </div>
      <div id="runtimeStatus" class="status">아직 확인하지 않았습니다.</div>
      <details>
        <summary>CUDA / vLLM / Time-slicing 상세</summary>
        <pre id="runtimeDetail"></pre>
      </details>
    </section>

    <div class="grid">
      <section>
        <h2>영상 입력</h2>
        <form id="analyzeForm">
          <div class="field">
            <label for="videoFile">영상 파일 업로드</label>
            <input id="videoFile" name="video_file" type="file" accept="video/*" />
            <div class="hint">파일 업로드와 URL을 둘 다 입력하면 파일 업로드를 우선합니다.</div>
          </div>
          <div class="field">
            <label for="videoUrl">영상 URL</label>
            <input id="videoUrl" name="video_url" placeholder="https://example.com/sample.mp4" />
          </div>
          <div class="row">
            <div class="field">
              <label for="frameCount">샘플 프레임 수</label>
              <input id="frameCount" name="frame_count" type="number" min="1" max="12" value="6" />
            </div>
            <div class="field">
              <label for="maxTokens">최대 토큰</label>
              <input id="maxTokens" name="max_tokens" type="number" min="64" max="2048" value="512" />
            </div>
          </div>
          <div class="row">
            <div class="field">
              <label for="modelId">모델 ID</label>
              <input id="modelId" name="model_id" value="Qwen/Qwen3-VL-2B-Instruct" />
            </div>
            <div class="field">
              <label for="endpoint">vLLM 엔드포인트</label>
              <input id="endpoint" name="endpoint" value="http://localhost:8000/v1/chat/completions" />
            </div>
          </div>
          <div class="field">
            <label for="prompt">분석 프롬프트</label>
            <textarea id="prompt" name="prompt">이 영상에서 발생한 주요 상황을 시간 순서대로 한국어로 요약해줘.</textarea>
          </div>
          <button id="analyzeBtn" type="submit">영상 분석 실행</button>
          <div id="analyzeStatus" class="status"></div>
        </form>
      </section>

      <section>
        <h2>분석 결과</h2>
        <div class="field">
          <label>추출 프레임</label>
          <div id="frames" class="cards"></div>
        </div>
        <div class="field">
          <label>VLM 응답</label>
          <pre id="answer">아직 분석하지 않았습니다.</pre>
        </div>
        <details>
          <summary>원본 JSON</summary>
          <pre id="rawJson"></pre>
        </details>
      </section>
    </div>
  </main>

  <script>
    async function refreshRuntime() {
      const [gpu, vllm, timeslicing] = await Promise.all([
        fetch("/api/gpu-status").then(r => r.json()),
        fetch("/api/vllm-status").then(r => r.json()),
        fetch("/api/timeslicing").then(r => r.json())
      ]);
      const vllmState = vllm.running
        ? "실행 중 - Qwen 모델이 GPU 메모리를 점유하고 있습니다. 테스트가 끝나면 vLLM 종료 / GPU 해제를 누르세요."
        : "중지됨 - GPU 모델 서버가 떠 있지 않습니다.";
      document.getElementById("runtimeStatus").textContent =
        `GPU: ${gpu.ok ? "확인됨" : "확인 실패"} / vLLM: ${vllmState} ${vllm.message || ""}`;
      document.getElementById("runtimeDetail").textContent =
        JSON.stringify({ gpu, vllm, timeslicing }, null, 2);
    }

    async function startVllm() {
      document.getElementById("runtimeStatus").textContent =
        "vLLM 컨테이너 시작 요청 중... 모델이 GPU에 로드되면 VRAM을 계속 점유합니다.";
      const res = await fetch("/api/start-vllm", { method: "POST" });
      const data = await res.json();
      document.getElementById("runtimeDetail").textContent = JSON.stringify(data, null, 2);
      if (!data.ok) {
        document.getElementById("runtimeStatus").textContent = data.message || "vLLM 시작 실패";
        return;
      }
      document.getElementById("runtimeStatus").textContent =
        "vLLM 컨테이너 시작 완료. 모델 다운로드/로딩 상태를 자동 확인합니다...";
      await waitForVllmReady();
    }

    async function waitForVllmReady() {
      const maxAttempts = 120;
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        const [gpu, vllm, timeslicing] = await Promise.all([
          fetch("/api/gpu-status").then(r => r.json()),
          fetch("/api/vllm-status").then(r => r.json()),
          fetch("/api/timeslicing").then(r => r.json())
        ]);
        document.getElementById("runtimeDetail").textContent =
          JSON.stringify({ gpu, vllm, timeslicing }, null, 2);
        if (vllm.running) {
          document.getElementById("runtimeStatus").textContent =
            "vLLM 준비 완료 - Qwen 모델이 GPU 메모리를 점유하고 있습니다. 이제 영상 분석을 실행할 수 있습니다.";
          return;
        }
        document.getElementById("runtimeStatus").textContent =
          `vLLM 로딩 대기 중 (${attempt}/${maxAttempts}) - 첫 실행은 이미지/모델 다운로드 때문에 오래 걸릴 수 있습니다.`;
      }
      document.getElementById("runtimeStatus").textContent =
        "vLLM 준비 확인 시간이 초과되었습니다. CUDA / vLLM 상세의 Docker logs를 확인하세요.";
    }

    async function stopVllm() {
      document.getElementById("runtimeStatus").textContent =
        "vLLM 컨테이너 종료 요청 중... 완료되면 Qwen 모델이 내려가고 GPU 메모리가 반환됩니다.";
      const res = await fetch("/api/stop-vllm", { method: "POST" });
      const data = await res.json();
      document.getElementById("runtimeDetail").textContent = JSON.stringify(data, null, 2);
      await refreshRuntime();
    }

    async function collectTimeslicingLogs() {
      document.getElementById("runtimeStatus").textContent =
        "time-slicing 관련 Kubernetes/GPU 로그를 수집 중입니다...";
      const res = await fetch("/api/timeslicing/logs", { method: "POST" });
      const data = await res.json();
      document.getElementById("runtimeDetail").textContent = JSON.stringify(data, null, 2);
      document.getElementById("runtimeStatus").textContent =
        `time-slicing 로그 수집 완료: ${data.log_dir || "로그 경로 없음"}`;
    }

    document.getElementById("analyzeForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = document.getElementById("analyzeBtn");
      const status = document.getElementById("analyzeStatus");
      const answer = document.getElementById("answer");
      const rawJson = document.getElementById("rawJson");
      const frames = document.getElementById("frames");

      button.disabled = true;
      status.textContent = "영상 저장, 프레임 추출, vLLM 분석 요청을 진행 중입니다...";
      answer.textContent = "";
      rawJson.textContent = "";
      frames.innerHTML = "";

      try {
        const formData = new FormData(event.target);
        const res = await fetch("/api/analyze-video", { method: "POST", body: formData });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || data.error || "분석 요청 실패");
        }
        status.textContent = `완료: ${data.video_info.sampled_frame_count}개 프레임 분석`;
        answer.textContent = data.answer || "(응답 텍스트 없음)";
        rawJson.textContent = JSON.stringify(data, null, 2);
        frames.innerHTML = data.frames.map(frame => `
          <div class="frame-card">
            <img src="${frame.preview_url}" alt="sample frame ${frame.index}" />
            <div>#${frame.index} / ${frame.timestamp_sec.toFixed(2)}초</div>
          </div>
        `).join("");
      } catch (error) {
        status.textContent = "오류 발생";
        answer.textContent = String(error);
      } finally {
        button.disabled = false;
      }
    });

    refreshRuntime();
  </script>
</body>
</html>
"""


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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """임시 테스트 UI를 반환합니다."""
    return INDEX_HTML


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
