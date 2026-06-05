"""
CUDA, vLLM Docker 컨테이너, time-slicing 안내 유틸리티.

처음 보는 사용자를 위한 개념 정리:

1. CUDA/GPU 확인
   - CUDA는 NVIDIA GPU에서 딥러닝 연산을 빠르게 수행하기 위한 실행 기반입니다.
   - 이 PoC에서 Qwen VLM 추론은 CPU가 아니라 RTX 4070 Ti GPU에서 처리하는 것을 목표로 합니다.
   - `nvidia-smi`는 NVIDIA 드라이버와 GPU 메모리 상태를 확인하는 가장 기본적인 명령입니다.
   - `nvidia-smi`가 실패하면 vLLM, Docker, 모델 문제가 아니라 GPU 드라이버/환경 문제부터 봐야 합니다.

2. vLLM 서빙
   - vLLM은 대형 언어모델/멀티모달 모델을 HTTP API로 서빙하기 위한 추론 서버입니다.
   - 이 앱은 모델을 직접 Python 프로세스에 로드하지 않고, Docker 컨테이너로 vLLM을 띄웁니다.
   - 이렇게 분리하면 앱 서버 오류와 GPU 추론 서버 오류를 따로 확인할 수 있습니다.
   - 화면에서 `vLLM 시작`을 누르면 내부적으로 `docker run ... vllm/vllm-openai`를 실행합니다.

3. time-slicing
   - time-slicing은 Kubernetes에서 하나의 GPU를 여러 Pod가 번갈아 쓰도록 노출하는 설정입니다.
   - GPU 메모리를 물리적으로 나누는 MIG와 다릅니다.
   - 이 로컬 PoC는 RTX 4070 Ti 단일 GPU 테스트이므로 time-slicing을 직접 적용하지 않습니다.
   - 대신 향후 Linux/Kubernetes GPU 노드에 적용할 YAML 초안과 주의사항을 화면에서 보여줍니다.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests


# vLLM이 로드할 기본 모델입니다.
# Qwen/Qwen3-VL-2B-Instruct는 경량 VLM 후보로, 영상에서 추출한 프레임 이미지를 분석하는 PoC에 사용합니다.
DEFAULT_MODEL_ID = os.environ.get("MODEL_ID", "Qwen/Qwen3-VL-2B-Instruct")

# vLLM의 chat/completions 엔드포인트입니다.
# FastAPI 앱은 이 주소로 분석 요청을 보내고, vLLM은 OpenAI 호환 형식의 JSON 응답을 반환합니다.
DEFAULT_VLLM_ENDPOINT = os.environ.get(
    "VLLM_ENDPOINT", "http://localhost:8000/v1/chat/completions"
)

# vLLM 서버가 떠 있는지 빠르게 확인하기 위한 models 엔드포인트입니다.
# 이 주소가 200 OK를 반환하면 최소한 vLLM HTTP 서버가 동작 중이라고 볼 수 있습니다.
DEFAULT_VLLM_MODELS_ENDPOINT = os.environ.get(
    "VLLM_MODELS_ENDPOINT", "http://localhost:8000/v1/models"
)

# Docker 컨테이너 이름입니다.
# 같은 이름을 고정해 두면 시작/중지 시 어떤 컨테이너를 제어하는지 명확합니다.
DEFAULT_CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "vlm-vllm-qwen")

# 공식 vLLM OpenAI 호환 Docker 이미지입니다.
# 모델 파일은 이 이미지 안에 포함되어 있지 않고, 실행 시 Hugging Face에서 내려받습니다.
DEFAULT_VLLM_IMAGE = os.environ.get("VLLM_IMAGE", "vllm/vllm-openai:latest")

# vLLM이 GPU 메모리를 어느 정도까지 사용하도록 허용할지 정합니다.
# RTX 4070 Ti 12GB 환경에서는 너무 높게 잡으면 앱/드라이버 여유 메모리가 부족할 수 있어 0.85부터 시작합니다.
DEFAULT_GPU_MEMORY_UTILIZATION = os.environ.get("GPU_MEMORY_UTILIZATION", "0.85")

# 모델이 한 번에 처리할 수 있는 최대 컨텍스트 길이입니다.
# 값이 커질수록 KV cache 메모리 사용량이 늘 수 있으므로 OOM 발생 시 4096으로 낮춰 테스트합니다.
DEFAULT_MAX_MODEL_LEN = os.environ.get("MAX_MODEL_LEN", "8192")


# vLLM 시작 작업 상태입니다.
# Docker image pull과 모델 컨테이너 시작은 오래 걸릴 수 있으므로 HTTP 요청 안에서 끝까지 기다리지 않습니다.
# 대신 백그라운드 thread가 작업하고, 화면은 /api/vllm-status polling으로 이 상태를 확인합니다.
VLLM_START_JOB: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "message": "vLLM 시작 작업이 아직 실행되지 않았습니다.",
    "started_at": None,
    "finished_at": None,
    "result": None,
}
VLLM_START_LOCK = threading.Lock()


def run_command(command: list[str], timeout: int = 30) -> dict[str, Any]:
    """
    외부 명령을 실행하고 stdout/stderr/exit code를 구조화해서 반환합니다.

    이 PoC는 Docker와 nvidia-smi 같은 운영체제 명령을 Python에서 호출합니다.
    명령 결과를 문자열로만 넘기면 화면에서 해석하기 어렵기 때문에,
    성공 여부(ok), 종료 코드(returncode), 표준 출력(stdout), 오류(stderr)를 JSON으로 반환합니다.
    """
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "command": command,
        }
    except FileNotFoundError as error:
        return {"ok": False, "error": str(error), "command": command}
    except subprocess.TimeoutExpired as error:
        return {"ok": False, "error": f"명령 시간이 초과되었습니다: {error}", "command": command}


def write_text_log(path: Path, title: str, result: dict[str, Any]) -> None:
    """
    명령 실행 결과를 사람이 읽기 쉬운 텍스트 로그로 저장합니다.

    time-slicing 검증은 Kubernetes 클러스터/노드 상태와 강하게 연결됩니다.
    실행 환경마다 실패 원인이 다르므로, 성공/실패 여부와 stdout/stderr를 그대로 파일에 남깁니다.
    """
    lines = [
        f"# {title}",
        "",
        f"command: {' '.join(str(part) for part in result.get('command', []))}",
        f"ok: {result.get('ok')}",
        f"returncode: {result.get('returncode', '')}",
        "",
        "## stdout",
        result.get("stdout", "") or "(empty)",
        "",
        "## stderr",
        result.get("stderr", "") or result.get("error", "") or "(empty)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def get_gpu_status() -> dict[str, Any]:
    """
    nvidia-smi를 통해 CUDA/GPU 상태를 확인합니다.

    여기서 확인하는 내용:
    - GPU 이름: 현재 테스트 장비가 RTX 4070 Ti인지 확인할 수 있습니다.
    - 전체 VRAM: 모델과 KV cache가 들어갈 수 있는지 판단하는 기준입니다.
    - 사용 중 VRAM: 이미 다른 프로세스가 GPU를 많이 쓰는지 확인합니다.
    - 드라이버 버전: CUDA/vLLM 컨테이너 호환성 문제를 볼 때 필요합니다.

    주의:
    - 이 함수는 CUDA 코드 자체를 실행하지 않습니다.
    - `nvidia-smi`가 성공하면 "드라이버와 GPU는 보인다"는 뜻이고,
      실제 vLLM 추론 가능 여부는 Docker GPU 전달과 모델 로딩까지 별도로 확인해야 합니다.
    """
    result = run_command(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if not result.get("ok"):
        return result

    gpus = []
    for line in result["stdout"].splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 4:
            gpus.append(
                {
                    "name": parts[0],
                    "memory_total_mb": parts[1],
                    "memory_used_mb": parts[2],
                    "driver_version": parts[3],
                }
            )
    result["gpus"] = gpus
    return result


def get_vllm_status() -> dict[str, Any]:
    """
    Docker 컨테이너와 vLLM models 엔드포인트 기준으로 실행 상태를 확인합니다.

    확인을 두 단계로 나누는 이유:
    - Docker 컨테이너가 존재해도 모델 다운로드/로딩 실패로 API가 죽어 있을 수 있습니다.
    - 반대로 `/v1/models`가 응답하면 HTTP 서버와 모델 서빙 레이어가 최소한 동작 중이라고 볼 수 있습니다.

    반환값의 `running`은 `/v1/models` 응답 성공 여부를 기준으로 합니다.
    """
    # Docker 상태를 먼저 봅니다.
    # Docker Desktop이 꺼져 있거나 Linux Engine이 실행 중이 아니면 여기서 실패합니다.
    docker_version = run_command(["docker", "version"], timeout=10)

    docker_result = run_command(
        ["docker", "ps", "-a", "--filter", f"name={DEFAULT_CONTAINER_NAME}", "--format", "{{.Names}}\t{{.Status}}"]
    )
    logs_result = run_command(["docker", "logs", "--tail", "80", DEFAULT_CONTAINER_NAME], timeout=10)
    endpoint_ok = False
    endpoint_error = ""
    try:
        response = requests.get(DEFAULT_VLLM_MODELS_ENDPOINT, timeout=3)
        endpoint_ok = response.ok
        endpoint_error = response.text[:500] if not response.ok else ""
    except requests.RequestException as error:
        endpoint_error = str(error)

    lifecycle_stage = infer_vllm_lifecycle_stage(endpoint_ok, docker_result, logs_result, endpoint_error)

    if endpoint_ok:
        message = "vLLM 서버가 실행 중이며 /v1/models 엔드포인트가 응답합니다."
    elif not docker_version.get("ok"):
        message = (
            "Docker가 실행 중이 아니거나 Docker Linux Engine에 연결할 수 없습니다. "
            "Docker Desktop을 먼저 실행한 뒤 vLLM 시작을 다시 누르세요."
        )
    else:
        message = (
            "vLLM 서버가 아직 응답하지 않습니다. vLLM 시작 버튼을 누른 뒤 모델 다운로드와 로딩이 끝날 때까지 기다리세요."
        )

    return {
        "running": endpoint_ok,
        "message": message,
        "container_name": DEFAULT_CONTAINER_NAME,
        "models_endpoint": DEFAULT_VLLM_MODELS_ENDPOINT,
        "docker_available": docker_version.get("ok", False),
        "docker_version": docker_version,
        "docker": docker_result,
        "logs": logs_result,
        "start_job": get_vllm_start_job(),
        "lifecycle_stage": lifecycle_stage,
        "config": {
            "model_id": DEFAULT_MODEL_ID,
            "container_name": DEFAULT_CONTAINER_NAME,
            "image": DEFAULT_VLLM_IMAGE,
            "gpu_memory_utilization": DEFAULT_GPU_MEMORY_UTILIZATION,
            "max_model_len": DEFAULT_MAX_MODEL_LEN,
            "hf_token_configured": bool(os.environ.get("HF_TOKEN")),
        },
        "endpoint_ok": endpoint_ok,
        "endpoint_error": endpoint_error,
    }


def infer_vllm_lifecycle_stage(
    endpoint_ok: bool,
    docker_result: dict[str, Any],
    logs_result: dict[str, Any],
    endpoint_error: str,
) -> str:
    """
    vLLM이 어느 단계에 있는지 화면에 보여주기 위한 간단한 분류입니다.

    Docker/vLLM은 모델 다운로드, weight 로딩, torch.compile, warmup을 거친 뒤에야
    `/v1/models`가 정상 응답합니다. 사용자는 이 단계 구분을 통해 "멈춤"인지 "준비 중"인지 판단할 수 있습니다.
    """
    if endpoint_ok:
        return "api_ready"
    docker_text = f"{docker_result.get('stdout', '')}\n{docker_result.get('stderr', '')}".lower()
    logs_text = f"{logs_result.get('stdout', '')}\n{logs_result.get('stderr', '')}\n{endpoint_error}".lower()
    if DEFAULT_CONTAINER_NAME.lower() not in docker_text:
        return "not_started"
    if "exited" in docker_text:
        return "failed"
    if "time spent downloading weights" in logs_text or "loading weights took" in logs_text:
        return "model_loading"
    if "downloading" in logs_text or ".incomplete" in logs_text or "hf hub" in logs_text:
        return "model_download"
    if "starting vllm server" in logs_text or "application startup complete" in logs_text:
        return "api_starting"
    if "error" in logs_text or "traceback" in logs_text:
        return "failed"
    return "starting"


def get_vllm_logs(lines: int = 120) -> dict[str, Any]:
    """
    vLLM 컨테이너 로그 tail을 반환합니다.

    화면에서 모델 다운로드/로딩/torch.compile 진행 상황을 바로 볼 수 있게 하는 용도입니다.
    """
    safe_lines = max(20, min(int(lines), 500))
    return run_command(["docker", "logs", "--tail", str(safe_lines), DEFAULT_CONTAINER_NAME], timeout=10)


def get_vllm_start_job() -> dict[str, Any]:
    """현재 vLLM 시작 백그라운드 작업 상태를 복사해서 반환합니다."""
    with VLLM_START_LOCK:
        return dict(VLLM_START_JOB)


def start_vllm_container() -> dict[str, Any]:
    """
    기존 컨테이너를 정리한 뒤 vLLM Docker 컨테이너를 백그라운드로 시작합니다.

    실제로 수행하는 일:
    1. 같은 이름의 이전 컨테이너를 제거합니다.
       - 이전 실행이 실패했거나 포트를 점유하고 있으면 새 실행이 막히기 때문입니다.
    2. Hugging Face 캐시 폴더를 컨테이너에 마운트합니다.
       - 모델을 매번 다시 다운로드하지 않기 위한 설정입니다.
    3. `--gpus all`로 Docker 컨테이너가 NVIDIA GPU를 볼 수 있게 합니다.
       - 이 옵션이 동작하려면 NVIDIA Container Toolkit이 설치되어 있어야 합니다.
    4. `--ipc=host`로 공유 메모리 제한 문제를 줄입니다.
       - PyTorch/vLLM 계열 워크로드에서 자주 필요한 설정입니다.
    5. `vllm/vllm-openai` 이미지에 `--model`을 넘겨 OpenAI 호환 API 서버를 시작합니다.

    이 함수는 모델 분석 요청을 보내지 않습니다.
    컨테이너 시작 후 `/api/vllm-status` 또는 `/v1/models`로 로딩 완료 여부를 확인해야 합니다.
    """
    with VLLM_START_LOCK:
        if VLLM_START_JOB["running"]:
            return {
                "ok": True,
                "message": "vLLM 시작 작업이 이미 백그라운드에서 진행 중입니다.",
                "start_job": dict(VLLM_START_JOB),
            }

    docker_version = run_command(["docker", "version"], timeout=10)
    if not docker_version.get("ok"):
        return {
            "ok": False,
            "message": (
                "Docker가 실행 중이 아니어서 vLLM 컨테이너를 시작할 수 없습니다. "
                "Docker Desktop을 실행하고 Linux Engine이 준비된 뒤 다시 시도하세요."
            ),
            "docker_version": docker_version,
        }

    with VLLM_START_LOCK:
        VLLM_START_JOB.update(
            {
                "running": True,
                "stage": "starting",
                "message": "vLLM 시작 백그라운드 작업을 준비 중입니다.",
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": None,
                "result": None,
            }
        )

    thread = threading.Thread(target=_start_vllm_container_worker, daemon=True)
    thread.start()
    return {
        "ok": True,
        "message": (
            "vLLM 시작 작업을 백그라운드로 보냈습니다. "
            "이미지 다운로드와 모델 로딩은 오래 걸릴 수 있으며 화면에서 자동으로 상태를 확인합니다."
        ),
        "start_job": get_vllm_start_job(),
    }


def _start_vllm_container_worker() -> None:
    """
    vLLM Docker 이미지 pull과 컨테이너 시작을 백그라운드에서 수행합니다.

    이 함수는 HTTP 요청 thread를 막지 않기 위해 별도 thread에서 실행됩니다.
    단계별 진행 상태는 VLLM_START_JOB에 저장되어 화면에서 확인할 수 있습니다.
    """
    result = _start_vllm_container_blocking()
    with VLLM_START_LOCK:
        VLLM_START_JOB.update(
            {
                "running": False,
                "stage": "done" if result.get("ok") else "failed",
                "message": result.get("message", ""),
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "result": result,
            }
        )


def _set_vllm_start_stage(stage: str, message: str) -> None:
    """백그라운드 시작 작업의 현재 단계를 갱신합니다."""
    with VLLM_START_LOCK:
        VLLM_START_JOB["stage"] = stage
        VLLM_START_JOB["message"] = message


def _start_vllm_container_blocking() -> dict[str, Any]:
    """실제 vLLM Docker 이미지 pull과 컨테이너 시작을 순서대로 수행합니다."""
    _set_vllm_start_stage("remove_previous", "이전 vLLM 컨테이너를 정리하는 중입니다.")
    remove_result = run_command(["docker", "rm", "-f", DEFAULT_CONTAINER_NAME], timeout=20)

    # 첫 실행에서는 vllm/vllm-openai 이미지 다운로드가 오래 걸릴 수 있습니다.
    # 기존 구현처럼 docker run 전체에 짧은 timeout을 걸면 이미지 pull 중에 실패로 오해할 수 있습니다.
    # 그래서 pull을 별도 단계로 분리하고, 충분한 시간을 줍니다.
    _set_vllm_start_stage("pull_image", "vLLM Docker 이미지를 다운로드하는 중입니다. 첫 실행은 오래 걸릴 수 있습니다.")
    pull_result = run_command(["docker", "pull", DEFAULT_VLLM_IMAGE], timeout=1800)
    if not pull_result.get("ok"):
        return {
            "ok": False,
            "message": (
                "vLLM Docker 이미지 다운로드에 실패했습니다. "
                "네트워크 상태와 Docker Desktop 상태를 확인하세요."
            ),
            "removed_previous": remove_result,
            "pulled_image": pull_result,
        }

    # HF_HOME은 Hugging Face 모델 캐시 위치입니다.
    # 컨테이너를 지웠다가 다시 실행해도 모델 파일을 재사용할 수 있게 호스트 폴더를 마운트합니다.
    hf_home = os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    Path(hf_home).mkdir(parents=True, exist_ok=True)

    # Docker 실행 명령의 앞부분입니다.
    # -d: 백그라운드 실행
    # --gpus all: 컨테이너에서 모든 NVIDIA GPU 사용 가능
    # -p 8000:8000: 호스트 8000 포트를 컨테이너 8000 포트에 연결
    # -v ...: Hugging Face 캐시를 컨테이너 내부 캐시 경로에 연결
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        DEFAULT_CONTAINER_NAME,
        "--gpus",
        "all",
        "--ipc=host",
        "-p",
        "8000:8000",
        "-v",
        f"{hf_home}:/root/.cache/huggingface",
    ]

    # 비공개/gated 모델을 쓰거나 Hugging Face rate limit을 피해야 할 때 토큰을 전달합니다.
    # 현재 기본 모델은 공개 모델이지만, 환경 확장 가능성을 위해 남겨 둡니다.
    if os.environ.get("HF_TOKEN"):
        command.extend(["-e", f"HF_TOKEN={os.environ['HF_TOKEN']}"])

    # vLLM 서버 실행 인자입니다.
    # --gpu-memory-utilization: vLLM이 사용할 GPU 메모리 비율
    # --max-model-len: 최대 컨텍스트 길이
    # --trust-remote-code: 모델 저장소의 커스텀 코드를 허용
    # Qwen 계열 VLM은 모델별 커스텀 처리 코드가 필요할 수 있어 이 옵션을 켭니다.
    command.extend(
        [
            DEFAULT_VLLM_IMAGE,
            "--model",
            DEFAULT_MODEL_ID,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--gpu-memory-utilization",
            DEFAULT_GPU_MEMORY_UTILIZATION,
            "--max-model-len",
            DEFAULT_MAX_MODEL_LEN,
            "--trust-remote-code",
        ]
    )
    # 이미지가 이미 준비된 상태에서 docker run -d는 컨테이너 ID만 출력하고 빠르게 종료되어야 합니다.
    # 모델 다운로드와 로딩은 컨테이너 내부에서 계속 진행되므로, 이후 /api/vllm-status로 확인합니다.
    _set_vllm_start_stage("run_container", "vLLM 컨테이너를 시작하는 중입니다.")
    start_result = run_command(command, timeout=120)
    return {
        "ok": start_result.get("ok", False),
        "message": (
            "vLLM 컨테이너 시작 명령을 실행했습니다. 모델 다운로드/로딩 완료까지 시간이 걸릴 수 있습니다."
            if start_result.get("ok")
            else "vLLM 컨테이너 시작 명령이 실패했습니다. started.stderr 또는 started.error를 확인하세요."
        ),
        "removed_previous": remove_result,
        "pulled_image": pull_result,
        "started": start_result,
    }


def stop_vllm_container() -> dict[str, Any]:
    """
    vLLM Docker 컨테이너를 중지하고 제거합니다.

    `docker stop`만 하면 컨테이너가 남아 다음 실행에서 이름 충돌이 날 수 있습니다.
    그래서 PoC에서는 `docker rm -f`로 중지와 제거를 한 번에 처리합니다.
    모델 캐시는 호스트 `HF_HOME`에 남아 있으므로 컨테이너를 지워도 모델 다운로드 파일은 보존됩니다.
    """
    with VLLM_START_LOCK:
        VLLM_START_JOB.update(
            {
                "running": False,
                "stage": "stopped",
                "message": "사용자 요청으로 vLLM 컨테이너를 중지했습니다.",
                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return run_command(["docker", "rm", "-f", DEFAULT_CONTAINER_NAME], timeout=30)


def get_timeslicing_summary(project_root: Path) -> dict[str, Any]:
    """
    현재 time-slicing 초안 파일과 적용 주의사항을 반환합니다.

    초심자용 설명:
    - 로컬 RTX 4070 Ti에서 Docker로 vLLM 하나를 띄우는 것은 time-slicing 적용 상황이 아닙니다.
    - time-slicing은 Kubernetes GPU 노드에서 NVIDIA device-plugin이 GPU 리소스를 여러 슬롯처럼 보이게 하는 설정입니다.
    - 예를 들어 replicas=2이면 Kubernetes 스케줄러는 GPU 1장을 2개의 `nvidia.com/gpu` 슬롯처럼 볼 수 있습니다.
    - 하지만 실제 VRAM은 그대로 12GB입니다. 두 Pod가 각각 큰 모델을 올리면 둘 다 성공한다는 보장은 없습니다.
    - 따라서 이 PoC에서는 설정 파일과 개념을 보여주고, 실제 적용은 별도 K8s GPU 서버에서 검증합니다.
    """
    manifest = project_root / "k8s" / "nvidia-device-plugin-timeslicing-config.yaml"
    return {
        "local_test_target": "RTX 4070 Ti 단일 GPU 로컬 테스트",
        "kubernetes_target": "NVIDIA k8s-device-plugin time-slicing",
        "manifest_path": str(manifest),
        "manifest_exists": manifest.exists(),
        "note": (
            "time-slicing은 GPU 접근 슬롯을 초과 할당하지만 GPU 메모리를 물리적으로 나누지 않습니다. "
            "실제 적용은 Linux/Kubernetes GPU 노드에서 검증해야 합니다."
        ),
    }


def _collect_timeslicing_logs_legacy(project_root: Path) -> dict[str, Any]:
    """
    time-slicing 검증에 필요한 Kubernetes/GPU 관련 로그를 수집합니다.

    수집하는 항목:
    - kubectl version: kubectl과 클러스터 연결 가능 여부 확인
    - kubectl config current-context: 현재 어느 클러스터를 보고 있는지 확인
    - kube-system Pod 목록: NVIDIA device-plugin Pod 존재 여부 확인
    - NVIDIA device-plugin 로그: time-slicing 설정 로드 오류나 GPU 리소스 노출 오류 확인
    - node describe: nvidia.com/gpu 리소스가 몇 개로 노출되는지 확인
    - app Pod 목록: vLLM Pod 스케줄링 상태 확인
    - 로컬 nvidia-smi: 현재 장비 GPU 상태 참고용

    로컬 Windows + Docker Desktop만 있는 환경에서는 Kubernetes GPU 노드가 없을 수 있습니다.
    그 경우에도 실패 결과를 logs/timeslicing/... 파일로 남겨 "아직 K8s 환경이 아니다"는 근거를 남깁니다.
    """
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_dir = project_root / "logs" / "timeslicing" / timestamp
    log_dir.mkdir(parents=True, exist_ok=True)

    commands: list[tuple[str, list[str], int]] = [
        ("kubectl-version", ["kubectl", "version", "--client=false"], 20),
        ("kubectl-context", ["kubectl", "config", "current-context"], 10),
        ("kube-system-pods", ["kubectl", "get", "pods", "-n", "kube-system", "-o", "wide"], 20),
        (
            "nvidia-device-plugin-logs",
            ["kubectl", "-n", "kube-system", "logs", "-l", "app=nvidia-device-plugin-daemonset", "--tail", "200"],
            30,
        ),
        ("nodes", ["kubectl", "get", "nodes", "-o", "wide"], 20),
        ("node-describe", ["kubectl", "describe", "nodes"], 30),
        ("all-pods", ["kubectl", "get", "pods", "-A", "-o", "wide"], 20),
        (
            "local-nvidia-smi",
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version", "--format=csv"],
            10,
        ),
    ]

    collected = []
    for name, command, timeout in commands:
        result = run_command(command, timeout=timeout)
        log_path = log_dir / f"{name}.txt"
        write_text_log(log_path, name, result)
        collected.append(
            {
                "name": name,
                "ok": result.get("ok", False),
                "path": str(log_path),
                "summary": (result.get("stdout") or result.get("stderr") or result.get("error") or "")[:500],
            }
        )

    manifest = project_root / "k8s" / "nvidia-device-plugin-timeslicing-config.yaml"
    if manifest.exists():
        manifest_copy = log_dir / "nvidia-device-plugin-timeslicing-config.yaml"
        manifest_copy.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "ok": any(item["ok"] for item in collected),
        "log_dir": str(log_dir),
        "items": collected,
        "note": (
            "일부 항목이 실패해도 정상입니다. 로컬에 Kubernetes GPU 노드가 없으면 kubectl 관련 로그는 실패 원인으로 저장됩니다."
        ),
    }


def collect_timeslicing_logs(project_root: Path) -> dict[str, Any]:
    """
    time-slicing 검증 로그를 1회 실행 단위 리포트로 수집합니다.

    로컬 Windows/Docker 환경에서는 Kubernetes time-slicing이 실제로 적용되지 않을 수 있습니다.
    이 함수의 목적은 성공만 기록하는 것이 아니라, 왜 검증이 불가능했는지까지 summary.json/summary.md에 남기는 것입니다.
    """
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = f"{timestamp}_{uuid.uuid4().hex[:6]}"
    log_dir = project_root / "logs" / "timeslicing" / run_id
    raw_dir = log_dir / "raw"
    manifest_dir = log_dir / "manifest"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = project_root / "k8s" / "nvidia-device-plugin-timeslicing-config.yaml"
    deployment = project_root / "k8s" / "vllm-qwen3-vl-2b-deployment.yaml"

    checks: list[dict[str, Any]] = []
    checks.append(
        _run_timeslicing_check(
            raw_dir,
            "kubectl_available",
            "kubectl 사용 가능 여부",
            ["kubectl", "version", "--client=true"],
            10,
            "kubectl 명령이 실행되었습니다.",
            "kubectl이 없거나 실행할 수 없습니다.",
        )
    )
    checks.append(
        _classify_cluster_check(
            _run_timeslicing_check(
                raw_dir,
                "cluster_connected",
                "Kubernetes 클러스터 연결",
                ["kubectl", "version", "--client=false"],
                20,
                "Kubernetes API 서버에 연결되었습니다.",
                "Kubernetes API 서버에 연결할 수 없습니다.",
            )
        )
    )
    checks.append(
        _run_timeslicing_check(
            raw_dir,
            "kubectl_context",
            "현재 kubectl context",
            ["kubectl", "config", "current-context"],
            10,
            "현재 kubectl context를 확인했습니다.",
            "현재 kubectl context를 확인하지 못했습니다.",
        )
    )
    checks.append(_check_manifest_files(raw_dir, manifest_dir, manifest, deployment))
    checks.append(
        _classify_manifest_dry_run(
            _run_timeslicing_check(
                raw_dir,
                "manifest_dry_run",
                "time-slicing manifest dry-run",
                ["kubectl", "apply", "--dry-run=client", "--validate=false", "-f", str(manifest)],
                20,
                "time-slicing manifest가 client dry-run 명령까지 도달했습니다.",
                "time-slicing manifest dry-run을 완료하지 못했습니다.",
            ),
            manifest.exists(),
        )
    )
    checks.append(
        _run_timeslicing_check(
            raw_dir,
            "kube_system_pods",
            "kube-system Pod 목록",
            ["kubectl", "get", "pods", "-n", "kube-system", "-o", "wide"],
            20,
            "kube-system Pod 목록을 수집했습니다.",
            "kube-system Pod 목록을 수집하지 못했습니다.",
        )
    )
    checks.append(
        _classify_device_plugin_check(
            _run_timeslicing_check(
                raw_dir,
                "device_plugin_found",
                "NVIDIA device-plugin 확인",
                ["kubectl", "-n", "kube-system", "logs", "-l", "app=nvidia-device-plugin-daemonset", "--tail", "200"],
                30,
                "NVIDIA device-plugin 로그를 수집했습니다.",
                "NVIDIA device-plugin 로그를 수집하지 못했습니다.",
            )
        )
    )
    checks.append(
        _run_timeslicing_check(
            raw_dir,
            "nodes",
            "Kubernetes node 목록",
            ["kubectl", "get", "nodes", "-o", "wide"],
            20,
            "Kubernetes node 목록을 수집했습니다.",
            "Kubernetes node 목록을 수집하지 못했습니다.",
        )
    )
    checks.append(
        _classify_gpu_resource_check(
            _run_timeslicing_check(
                raw_dir,
                "gpu_resource_visible",
                "node GPU 리소스 노출 확인",
                ["kubectl", "describe", "nodes"],
                30,
                "node describe 결과를 수집했습니다.",
                "node describe 결과를 수집하지 못했습니다.",
            )
        )
    )
    checks.append(
        _run_timeslicing_check(
            raw_dir,
            "all_pods",
            "전체 Pod 목록",
            ["kubectl", "get", "pods", "-A", "-o", "wide"],
            20,
            "전체 Pod 목록을 수집했습니다.",
            "전체 Pod 목록을 수집하지 못했습니다.",
        )
    )
    checks.append(
        _run_timeslicing_check(
            raw_dir,
            "local_nvidia_smi",
            "로컬 GPU 상태",
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version", "--format=csv"],
            10,
            "로컬 nvidia-smi 결과를 수집했습니다.",
            "로컬 nvidia-smi 결과를 수집하지 못했습니다.",
        )
    )

    overall_status = _calculate_timeslicing_overall_status(checks)
    summary_json_path = log_dir / "summary.json"
    summary_md_path = log_dir / "summary.md"
    report = {
        "ok": overall_status in {"success", "partial", "not_available"},
        "overall_status": overall_status,
        "run_id": run_id,
        "created_at": created_at,
        "log_dir": str(log_dir),
        "summary_json_path": str(summary_json_path),
        "summary_md_path": str(summary_md_path),
        "raw_log_dir": str(raw_dir),
        "checks": checks,
        "items": [
            {
                "name": check["id"],
                "ok": check["status"] == "success",
                "path": check["raw_log_path"],
                "summary": check["summary"],
            }
            for check in checks
        ],
        "paths": {
            "log_dir": str(log_dir),
            "raw_log_dir": str(raw_dir),
            "manifest_dir": str(manifest_dir),
            "summary_json": str(summary_json_path),
            "summary_md": str(summary_md_path),
        },
        "environment_note": (
            "로컬 Windows/Docker 환경에서는 Kubernetes GPU 노드가 없을 수 있습니다. "
            "이 경우 not_available 또는 partial 결과도 유효한 검증 근거입니다."
        ),
    }
    summary_json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_md_path.write_text(_render_timeslicing_summary_markdown(report), encoding="utf-8")
    return report


def _run_timeslicing_check(
    raw_dir: Path,
    check_id: str,
    label: str,
    command: list[str],
    timeout: int,
    success_summary: str,
    failure_summary: str,
) -> dict[str, Any]:
    """명령 하나를 실행하고 check 결과와 raw 로그 파일을 생성합니다."""
    result = run_command(command, timeout=timeout)
    raw_log_path = raw_dir / f"{check_id}.txt"
    write_text_log(raw_log_path, label, result)
    status = "success" if result.get("ok") else "failed"
    reason_code = "ok" if result.get("ok") else _infer_reason_code(result)
    output = result.get("stdout") or result.get("stderr") or result.get("error") or ""
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "reason_code": reason_code,
        "summary": success_summary if result.get("ok") else f"{failure_summary} {output[:300]}".strip(),
        "command": command,
        "raw_log_path": str(raw_log_path),
    }


def _check_manifest_files(raw_dir: Path, manifest_dir: Path, manifest: Path, deployment: Path) -> dict[str, Any]:
    """time-slicing/vLLM manifest 파일 존재 여부와 복사본을 기록합니다."""
    raw_log_path = raw_dir / "manifest_files.txt"
    existing = []
    missing = []
    for path in [manifest, deployment]:
        if path.exists():
            target = manifest_dir / path.name
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            existing.append(str(path))
        else:
            missing.append(str(path))
    raw_log_path.write_text(
        "existing:\n" + "\n".join(existing or ["(empty)"]) + "\n\nmissing:\n" + "\n".join(missing or ["(empty)"]),
        encoding="utf-8",
    )
    if missing:
        return {
            "id": "manifest_files",
            "label": "manifest 파일 존재 여부",
            "status": "failed",
            "reason_code": "manifest_missing",
            "summary": "필수 manifest 파일 일부가 없습니다.",
            "command": [],
            "raw_log_path": str(raw_log_path),
        }
    return {
        "id": "manifest_files",
        "label": "manifest 파일 존재 여부",
        "status": "success",
        "reason_code": "ok",
        "summary": "time-slicing 및 vLLM manifest 파일을 확인하고 복사했습니다.",
        "command": [],
        "raw_log_path": str(raw_log_path),
    }


def _infer_reason_code(result: dict[str, Any]) -> str:
    """명령 실패 결과에서 대표 reason_code를 추론합니다."""
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}\n{result.get('error', '')}".lower()
    command = " ".join(result.get("command", [])).lower()
    if "kubectl" in command and ("not recognized" in text or "file not found" in text or "no such file" in text):
        return "kubectl_not_installed"
    if "couldn't get current server api group list" in text or "the server could not find the requested resource" in text:
        return "cluster_not_connected"
    if "connection refused" in text or "unable to connect" in text or "no configuration has been provided" in text:
        return "cluster_not_connected"
    if "no resources found" in text and "nvidia" in text:
        return "device_plugin_missing"
    if "describe" in command and "nvidia.com/gpu" not in text:
        return "gpu_resource_not_visible"
    return "unknown"


def _classify_cluster_check(check: dict[str, Any]) -> dict[str, Any]:
    """클러스터 연결 실패를 not_available로 분류합니다."""
    if check["status"] != "success" and check["reason_code"] == "cluster_not_connected":
        check["status"] = "not_available"
        check["summary"] = "Kubernetes 클러스터에 연결되지 않았습니다. 로컬 Windows PoC에서는 정상적인 미검증 상태일 수 있습니다."
    return check


def _classify_manifest_dry_run(check: dict[str, Any], manifest_exists: bool) -> dict[str, Any]:
    """manifest dry-run 실패 원인을 manifest 부재 또는 클러스터 미연결로 분류합니다."""
    if not manifest_exists:
        check["status"] = "failed"
        check["reason_code"] = "manifest_missing"
        check["summary"] = "time-slicing manifest 파일이 없어 dry-run을 수행할 수 없습니다."
    elif check["status"] != "success" and check["reason_code"] == "cluster_not_connected":
        check["status"] = "not_available"
        check["summary"] = "kubectl dry-run이 클러스터 discovery 단계에서 실패했습니다. 연결된 K8s API 서버가 필요합니다."
    return check


def _classify_device_plugin_check(check: dict[str, Any]) -> dict[str, Any]:
    """NVIDIA device-plugin 로그 수집 결과를 분류합니다."""
    if check["status"] == "success":
        return check
    if check["reason_code"] == "cluster_not_connected":
        check["status"] = "not_available"
        check["summary"] = "클러스터에 연결되지 않아 NVIDIA device-plugin을 확인할 수 없습니다."
    else:
        check["reason_code"] = "device_plugin_missing"
        check["summary"] = "NVIDIA device-plugin 로그를 찾지 못했습니다. device-plugin 설치 또는 label을 확인해야 합니다."
    return check


def _classify_gpu_resource_check(check: dict[str, Any]) -> dict[str, Any]:
    """node describe 출력에서 nvidia.com/gpu 노출 여부를 확인합니다."""
    raw_path = Path(check["raw_log_path"])
    text = raw_path.read_text(encoding="utf-8", errors="replace").lower() if raw_path.exists() else ""
    if check["status"] == "success" and "nvidia.com/gpu" in text:
        check["summary"] = "node describe 결과에서 nvidia.com/gpu 리소스를 확인했습니다."
        return check
    if check["reason_code"] == "cluster_not_connected":
        check["status"] = "not_available"
        check["summary"] = "클러스터에 연결되지 않아 GPU 리소스 노출 여부를 확인할 수 없습니다."
    else:
        check["status"] = "failed"
        check["reason_code"] = "gpu_resource_not_visible"
        check["summary"] = "node describe 결과에서 nvidia.com/gpu 리소스를 확인하지 못했습니다."
    return check


def _calculate_timeslicing_overall_status(checks: list[dict[str, Any]]) -> str:
    """핵심 check 상태를 기준으로 전체 결과를 계산합니다."""
    core_ids = {"kubectl_available", "cluster_connected", "device_plugin_found", "gpu_resource_visible"}
    core = [check for check in checks if check["id"] in core_ids]
    statuses = {check["id"]: check["status"] for check in core}
    if statuses.get("kubectl_available") != "success" or statuses.get("cluster_connected") == "not_available":
        return "not_available"
    if core and all(check["status"] == "success" for check in core):
        return "success"
    if any(check["status"] == "success" for check in checks):
        return "partial"
    return "failed"


def _render_timeslicing_summary_markdown(report: dict[str, Any]) -> str:
    """summary.md에 저장할 사람이 읽는 검증 리포트를 생성합니다."""
    lines = [
        "# Time-slicing 검증 리포트",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- created_at: `{report['created_at']}`",
        f"- overall_status: `{report['overall_status']}`",
        f"- log_dir: `{report['log_dir']}`",
        "",
        "## Check 결과",
        "",
        "| 상태 | 항목 | 원인 | 요약 |",
        "| --- | --- | --- | --- |",
    ]
    for check in report["checks"]:
        lines.append(
            f"| `{check['status']}` | {check['label']} | `{check['reason_code']}` | {check['summary'].replace('|', '/')} |"
        )
    lines.extend(["", "## 환경 메모", "", report["environment_note"], ""])
    return "\n".join(lines)
