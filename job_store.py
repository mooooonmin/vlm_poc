"""
영상 분석 작업 상태를 저장하고 파일로 남기는 작은 job 저장소입니다.

이 PoC는 실제 운영용 큐 서버나 데이터베이스를 목표로 하지 않습니다.
대신 여러 영상을 연속 테스트할 때 각 요청의 상태와 실패 원인을 구분할 수 있도록
메모리 딕셔너리와 `tmp/jobs/{job_id}/job.json` 파일을 함께 사용합니다.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


JOB_STATUSES = {"queued", "running", "done", "failed"}
STORE_LOCK = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}


def now_text() -> str:
    """화면과 로그에서 읽기 쉬운 현재 시각 문자열을 반환합니다."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def create_job(
    base_dir: Path,
    source: dict[str, Any],
    settings: dict[str, Any],
    batch_id: str | None = None,
    batch_index: int | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """
    새 분석 작업을 만들고 `queued` 상태로 저장합니다.

    - job_id는 화면 polling과 결과 조회에 쓰는 고유 ID입니다.
    - job_dir은 업로드 파일, 다운로드 영상, job.json을 한곳에 묶는 폴더입니다.
    - settings에는 프레임 수, 모델 ID, vLLM endpoint 같은 분석 설정을 저장합니다.
    """
    job_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    job_dir = base_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job = {
        "job_id": job_id,
        "batch_id": batch_id,
        "batch_index": batch_index,
        "batch_size": batch_size,
        "status": "queued",
        "message": "분석 대기 중입니다.",
        "created_at": now_text(),
        "updated_at": now_text(),
        "queued_at": now_text(),
        "started_at": None,
        "finished_at": None,
        "frame_extract_started_at": None,
        "frame_extract_finished_at": None,
        "vllm_request_started_at": None,
        "vllm_request_finished_at": None,
        "duration_ms": None,
        "frame_extract_duration_ms": None,
        "vllm_duration_ms": None,
        "failure_stage": None,
        "failure_reason": None,
        "korean_check": None,
        "korean_retry_used": False,
        "korean_repair_used": False,
        "korean_fallback_used": False,
        "loop_checks": {},
        "gpu_snapshots": [],
        "job_dir": str(job_dir),
        "source": source,
        "settings": settings,
        "worker_id": None,
        "worker_endpoint": None,
        "worker_status": [],
        "video_info": None,
        "frames": [],
        "answer": "",
        "raw": None,
        "error": None,
        "events": [],
    }
    _save_job(job)
    return job


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    """
    작업 상태를 갱신하고 즉시 job.json에 저장합니다.

    상태 변경을 파일에 바로 남기는 이유는 브라우저가 닫히거나 서버 로그를 놓쳐도
    `tmp/jobs/{job_id}/job.json`에서 마지막 상태를 다시 확인하기 위해서입니다.
    """
    with STORE_LOCK:
        if job_id not in JOBS:
            raise KeyError(f"알 수 없는 job_id입니다: {job_id}")
        job = JOBS[job_id]
        if "status" in changes and changes["status"] not in JOB_STATUSES:
            raise ValueError(f"지원하지 않는 작업 상태입니다: {changes['status']}")
        job.update(changes)
        job["updated_at"] = now_text()
        _append_event(job, changes.get("message"))
        _write_job_file(job)
        return dict(job)


def get_job(job_id: str) -> dict[str, Any] | None:
    """메모리에 있는 작업을 조회합니다."""
    with STORE_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """최근 작업 목록을 최신순으로 반환합니다."""
    with STORE_LOCK:
        jobs = sorted(JOBS.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        return [dict(job) for job in jobs[:limit]]


def list_batch_jobs(batch_id: str) -> list[dict[str, Any]]:
    """같은 batch_id로 묶인 작업을 입력 순서 기준으로 반환합니다."""
    with STORE_LOCK:
        jobs = [dict(job) for job in JOBS.values() if job.get("batch_id") == batch_id]
    return sorted(jobs, key=lambda item: int(item.get("batch_index") or 0))


def get_job_stats(limit: int = 50) -> dict[str, Any]:
    """
    최근 작업의 성공/실패/처리시간 요약을 반환합니다.

    오래된 job.json에는 duration_ms 같은 새 필드가 없을 수 있습니다.
    이 경우 통계 계산에서 해당 시간값만 제외하고, 성공/실패 개수는 그대로 반영합니다.
    """
    jobs = list_jobs(limit=limit)
    total = len(jobs)
    status_counts = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    worker_counts: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    durations = []
    korean_ok_count = 0
    korean_failed_count = 0
    korean_retry_count = 0
    korean_repair_count = 0
    korean_fallback_count = 0
    gpu_snapshot_job_count = 0

    for job in jobs:
        status = str(job.get("status", ""))
        if status in status_counts:
            status_counts[status] += 1

        worker_id = job.get("worker_id") or "미배정"
        worker_counts[str(worker_id)] = worker_counts.get(str(worker_id), 0) + 1

        if status == "failed":
            reason = job.get("failure_reason") or job.get("failure_stage") or "unknown"
            failure_counts[str(reason)] = failure_counts.get(str(reason), 0) + 1

        duration_ms = job.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            durations.append(float(duration_ms))

        korean_check = job.get("korean_check")
        if isinstance(korean_check, dict):
            if korean_check.get("ok"):
                korean_ok_count += 1
            else:
                korean_failed_count += 1
        if job.get("korean_retry_used"):
            korean_retry_count += 1
        if job.get("korean_repair_used"):
            korean_repair_count += 1
        if job.get("korean_fallback_used"):
            korean_fallback_count += 1
        if job.get("gpu_snapshots"):
            gpu_snapshot_job_count += 1

    average_duration_ms = round(sum(durations) / len(durations), 1) if durations else None
    return {
        "limit": limit,
        "total": total,
        "status_counts": status_counts,
        "success_count": status_counts["done"],
        "failed_count": status_counts["failed"],
        "average_duration_ms": average_duration_ms,
        "timed_job_count": len(durations),
        "worker_counts": worker_counts,
        "failure_counts": failure_counts,
        "korean_ok_count": korean_ok_count,
        "korean_failed_count": korean_failed_count,
        "korean_retry_count": korean_retry_count,
        "korean_repair_count": korean_repair_count,
        "korean_fallback_count": korean_fallback_count,
        "gpu_snapshot_job_count": gpu_snapshot_job_count,
    }


def load_existing_jobs(base_dir: Path) -> None:
    """
    서버 재시작 후에도 이전 job.json을 최근 작업 목록에 다시 표시합니다.

    실제 운영 DB는 아니므로, 파일이 깨져 있거나 읽을 수 없는 작업은 조용히 건너뜁니다.
    """
    jobs_root = base_dir / "jobs"
    if not jobs_root.exists():
        return
    for job_file in jobs_root.glob("*/job.json"):
        try:
            job = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(job, dict) and job.get("job_id"):
            with STORE_LOCK:
                JOBS[job["job_id"]] = job


def _save_job(job: dict[str, Any]) -> None:
    """새 작업을 메모리와 파일에 동시에 저장합니다."""
    with STORE_LOCK:
        _append_event(job, job.get("message"))
        JOBS[job["job_id"]] = job
        _write_job_file(job)


def _append_event(job: dict[str, Any], message: str | None) -> None:
    """작업 상태 변화 이력을 짧게 남깁니다."""
    if not message:
        return
    events = job.setdefault("events", [])
    events.append({"at": now_text(), "status": job.get("status"), "message": message})
    job["events"] = events[-50:]


def _write_job_file(job: dict[str, Any]) -> None:
    """job.json을 UTF-8 JSON으로 저장합니다."""
    job_dir = Path(job["job_dir"])
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
