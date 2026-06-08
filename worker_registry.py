"""
vLLM worker 상태를 관리하는 작은 registry입니다.

이 프로젝트에서 말하는 worker는 Python thread가 아니라 "요청을 받을 수 있는 vLLM API 서버 1개"입니다.
로컬 Windows 테스트에서는 보통 `http://localhost:8000/v1/chat/completions` 1개만 사용합니다.
Kubernetes time-slicing 테스트에서는 vLLM Pod/Service를 여러 개 띄우고 `VLLM_WORKERS` 환경변수에
각 endpoint를 쉼표로 넣어 여러 영상 분석 job을 서로 다른 vLLM 서버로 배정할 수 있습니다.

중요:
- time-slicing은 GPU 메모리를 물리적으로 나누지 않습니다.
- 이 registry는 "어떤 요청을 어떤 vLLM 서버로 보낼지"만 결정합니다.
- 실제 GPU 공유 여부는 Kubernetes NVIDIA device-plugin 설정과 vLLM Pod 배치에 달려 있습니다.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests

from runtime_utils import DEFAULT_VLLM_ENDPOINT


WORKER_LOCK = threading.Lock()


def now_text() -> str:
    """화면과 job 로그에서 읽기 쉬운 현재 시각 문자열을 반환합니다."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def normalize_chat_endpoint(endpoint: str) -> str:
    """사용자가 입력한 endpoint 문자열을 `/v1/chat/completions` URL로 맞춥니다."""
    endpoint = endpoint.strip().rstrip("/")
    if endpoint.endswith("/v1/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/chat/completions"
    return f"{endpoint}/v1/chat/completions"


def models_endpoint_from_chat_endpoint(endpoint: str) -> str:
    """chat completions endpoint에서 readiness 확인용 `/v1/models` endpoint를 계산합니다."""
    if endpoint.endswith("/v1/chat/completions"):
        return endpoint[: -len("/chat/completions")] + "/models"
    return endpoint.rstrip("/") + "/models"


def parse_worker_endpoints() -> list[str]:
    """
    `VLLM_WORKERS` 환경변수를 worker endpoint 목록으로 변환합니다.

    예:
    - 미설정: `DEFAULT_VLLM_ENDPOINT` 1개 사용
    - 설정: `http://localhost:8000/v1/chat/completions,http://localhost:8001/v1/chat/completions`
    """
    raw_value = os.environ.get("VLLM_WORKERS", "").strip()
    if not raw_value:
        return [normalize_chat_endpoint(DEFAULT_VLLM_ENDPOINT)]
    endpoints = [normalize_chat_endpoint(item) for item in raw_value.split(",") if item.strip()]
    return endpoints or [normalize_chat_endpoint(DEFAULT_VLLM_ENDPOINT)]


def build_initial_workers() -> list[dict[str, Any]]:
    """환경변수 기준으로 worker 초기 상태를 만듭니다."""
    workers = []
    for index, endpoint in enumerate(parse_worker_endpoints(), start=1):
        workers.append(
            {
                "worker_id": f"worker-{index}",
                "endpoint": endpoint,
                "models_endpoint": models_endpoint_from_chat_endpoint(endpoint),
                "status": "unknown",
                "active_job_id": None,
                "last_error": "",
                "last_checked_at": None,
                "last_assigned_at": None,
                "completed_jobs": 0,
                "failed_jobs": 0,
            }
        )
    return workers


WORKERS: list[dict[str, Any]] = build_initial_workers()


def list_workers() -> list[dict[str, Any]]:
    """현재 worker 상태를 화면/API에 반환할 수 있는 dict 목록으로 복사합니다."""
    with WORKER_LOCK:
        return [dict(worker) for worker in WORKERS]


def refresh_workers(timeout_sec: int = 5) -> list[dict[str, Any]]:
    """
    각 worker의 `/v1/models` endpoint를 호출해 준비 상태를 갱신합니다.

    `/v1/models`가 응답하면 `ready`, 연결 실패/타임아웃이면 `error`로 표시합니다.
    이미 job을 처리 중인 worker는 `busy` 상태를 유지하되, endpoint 응답 여부만 last_error에 반영합니다.
    """
    with WORKER_LOCK:
        targets = [dict(worker) for worker in WORKERS]

    for target in targets:
        status = "ready"
        last_error = ""
        try:
            response = requests.get(target["models_endpoint"], timeout=timeout_sec)
            response.raise_for_status()
        except Exception as error:
            status = "error"
            last_error = str(error)

        with WORKER_LOCK:
            worker = _find_worker_locked(target["worker_id"])
            if not worker:
                continue
            worker["last_checked_at"] = now_text()
            worker["last_error"] = last_error
            if worker.get("active_job_id"):
                worker["status"] = "busy"
            else:
                worker["status"] = status

    return list_workers()


def acquire_ready_worker(job_id: str) -> dict[str, Any] | None:
    """
    비어 있고 ready 상태인 worker 하나를 job에 배정합니다.

    여러 영상 요청이 들어오면 dispatcher가 이 함수를 호출해 사용 가능한 vLLM 서버를 찾습니다.
    사용할 수 있는 worker가 없으면 None을 반환하고, job은 queued 상태로 대기합니다.
    """
    with WORKER_LOCK:
        for worker in WORKERS:
            if worker.get("status") == "ready" and not worker.get("active_job_id"):
                worker["status"] = "busy"
                worker["active_job_id"] = job_id
                worker["last_assigned_at"] = now_text()
                return dict(worker)
    return None


def release_worker(worker_id: str, success: bool, error_message: str = "") -> None:
    """job 처리가 끝난 worker를 다시 ready/error 상태로 돌립니다."""
    with WORKER_LOCK:
        worker = _find_worker_locked(worker_id)
        if not worker:
            return
        worker["active_job_id"] = None
        worker["last_error"] = error_message
        if success:
            worker["completed_jobs"] = int(worker.get("completed_jobs", 0)) + 1
            worker["status"] = "ready"
        else:
            worker["failed_jobs"] = int(worker.get("failed_jobs", 0)) + 1
            worker["status"] = "error"


def _find_worker_locked(worker_id: str) -> dict[str, Any] | None:
    """WORKER_LOCK을 잡은 상태에서만 호출하는 내부 검색 함수입니다."""
    for worker in WORKERS:
        if worker.get("worker_id") == worker_id:
            return worker
    return None
