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


def create_job(base_dir: Path, source: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
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
        "status": "queued",
        "message": "분석 대기 중입니다.",
        "created_at": now_text(),
        "updated_at": now_text(),
        "job_dir": str(job_dir),
        "source": source,
        "settings": settings,
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
