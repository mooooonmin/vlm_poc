"""
영상 분석 작업 상태를 저장하고 파일로 남기는 작은 job 저장소입니다.

이 PoC는 실제 운영용 큐 서버나 데이터베이스를 목표로 하지 않습니다.
대신 여러 영상을 연속 테스트할 때 각 요청의 상태와 실패 원인을 구분할 수 있도록
메모리 딕셔너리와 `tmp/jobs/{job_id}/job.json` 파일을 함께 사용합니다.
"""

from __future__ import annotations

import json
import shutil
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


def cleanup_finished_jobs(tmp_dir: Path, frame_dir: Path, logs_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    """
    완료/실패한 job과 테스트용 임시 파일을 정리합니다.

    삭제 대상:
    - status가 `done` 또는 `failed`인 job의 `tmp/jobs/{job_id}` 폴더
    - `queued`/`running` job이 참조하지 않는 `tmp/frames/*` 파일
    - 메모리에는 없지만 `tmp/jobs`에 남아 있는 고아 job 폴더
    - `tmp/evaluation_samples`, `tmp/validation` 테스트 샘플 폴더
    - `tmp/layout_*.png` 화면 검증 스크린샷
    - `logs/evaluation/*`, `logs/timeslicing/*` 자동 생성 리포트 폴더
    - 메모리에 남아 있는 해당 job 상태

    삭제하지 않는 대상:
    - `queued` 또는 `running` 상태 job
    - 진행 중 job의 job_dir
    - 진행 중 job의 frames 목록이 참조하는 프레임 파일
    - tmp_dir/frame_dir 밖에 있는 경로

    dry_run=True이면 실제 삭제 없이 삭제 예정 개수와 예상 용량만 계산합니다.
    """
    tmp_root = tmp_dir.resolve()
    frame_root = frame_dir.resolve()
    logs_root = logs_dir.resolve()
    jobs_root = tmp_root / "jobs"
    with STORE_LOCK:
        jobs_snapshot = [dict(job) for job in JOBS.values()]

    targets = [job for job in jobs_snapshot if job.get("status") in {"done", "failed"}]
    skipped = [job for job in jobs_snapshot if job.get("status") in {"queued", "running"}]
    active_job_dirs = {
        Path(str(job.get("job_dir"))).resolve()
        for job in skipped
        if job.get("job_dir")
    }
    active_frame_paths = {
        Path(str(frame.get("path"))).resolve()
        for job in skipped
        for frame in (job.get("frames") or [])
        if frame.get("path")
    }
    deleted_job_ids: list[str] = []
    errors: list[dict[str, str]] = []
    deleted_frame_files = 0
    deleted_job_dirs = 0
    deleted_orphan_job_dirs = 0
    deleted_extra_dirs = 0
    deleted_extra_files = 0
    deleted_log_dirs = 0
    freed_bytes = 0
    planned_frame_paths: set[Path] = set()
    planned_job_dirs: set[Path] = set()

    for job in targets:
        job_id = str(job.get("job_id") or "")

        # 프레임 파일은 화면 미리보기용으로 tmp/frames에 따로 저장됩니다.
        # job.json에 기록된 경로만 지우고, frame_dir 밖 경로는 실수 방지를 위해 건너뜁니다.
        for frame in job.get("frames") or []:
            frame_path = Path(str(frame.get("path") or ""))
            if not _is_within(frame_path, frame_root):
                continue
            if not frame_path.exists():
                continue
            try:
                freed_bytes += _delete_path(frame_path, dry_run)
                planned_frame_paths.add(frame_path.resolve())
                deleted_frame_files += 1
            except OSError as error:
                errors.append({"job_id": job_id, "path": str(frame_path), "error": str(error)})

        job_dir = Path(str(job.get("job_dir") or ""))
        if not job_id or job_dir.name != job_id or not job_dir.exists() or not _is_within(job_dir, jobs_root):
            continue
        try:
            freed_bytes += _delete_path(job_dir, dry_run)
            planned_job_dirs.add(job_dir.resolve())
            deleted_job_dirs += 1
            deleted_job_ids.append(job_id)
        except OSError as error:
            errors.append({"job_id": job_id, "path": str(job_dir), "error": str(error)})

    # 메모리에 남아 있지 않은 이전 테스트 job 폴더도 tmp/jobs 아래에 있으면 정리합니다.
    # 단, 현재 queued/running job 폴더는 건너뜁니다.
    if jobs_root.exists():
        for job_dir in jobs_root.iterdir():
            if not job_dir.is_dir():
                continue
            resolved_job_dir = job_dir.resolve()
            if resolved_job_dir in active_job_dirs:
                continue
            if resolved_job_dir in planned_job_dirs:
                continue
            try:
                freed_bytes += _delete_path(job_dir, dry_run)
                deleted_orphan_job_dirs += 1
            except OSError as error:
                errors.append({"job_id": "", "path": str(job_dir), "error": str(error)})

    # tmp/frames는 공용 미리보기 폴더라 job.json에 연결되지 않은 고아 jpg가 남을 수 있습니다.
    # 진행 중 job이 참조하는 프레임만 보호하고 나머지는 모두 삭제합니다.
    if frame_root.exists():
        for frame_path in frame_root.iterdir():
            if not frame_path.is_file():
                continue
            if frame_path.resolve() in active_frame_paths:
                continue
            if frame_path.resolve() in planned_frame_paths:
                continue
            try:
                freed_bytes += _delete_path(frame_path, dry_run)
                deleted_frame_files += 1
            except OSError as error:
                errors.append({"job_id": "", "path": str(frame_path), "error": str(error)})

    # evaluation_runner와 수동 검증에서 만든 샘플 영상 폴더도 tmp 산출물이므로 버튼 정리 대상에 포함합니다.
    for extra_dir_name in ("evaluation_samples", "validation"):
        extra_dir = tmp_root / extra_dir_name
        if extra_dir.exists() and _is_within(extra_dir, tmp_root):
            try:
                freed_bytes += _delete_path(extra_dir, dry_run)
                deleted_extra_dirs += 1
            except OSError as error:
                errors.append({"job_id": "", "path": str(extra_dir), "error": str(error)})

    # UI 검증용 headless screenshot도 tmp 산출물입니다.
    for extra_file in tmp_root.glob("layout_*.png"):
        if extra_file.is_file() and _is_within(extra_file, tmp_root):
            try:
                freed_bytes += _delete_path(extra_file, dry_run)
                deleted_extra_files += 1
            except OSError as error:
                errors.append({"job_id": "", "path": str(extra_file), "error": str(error)})

    # 자동 평가와 time-slicing 검증은 매 실행마다 logs 하위에 리포트 폴더를 만듭니다.
    # 사람이 관리하는 docs/TEST_RESULTS.md는 삭제 대상이 아니며, logs 전체가 아니라 생성 리포트 폴더만 정리합니다.
    for log_category in ("evaluation", "timeslicing"):
        category_root = logs_root / log_category
        if not category_root.exists() or not _is_within(category_root, logs_root):
            continue
        for log_dir in category_root.iterdir():
            if not log_dir.is_dir() or not _is_within(log_dir, category_root):
                continue
            try:
                freed_bytes += _delete_path(log_dir, dry_run)
                deleted_log_dirs += 1
            except OSError as error:
                errors.append({"job_id": "", "path": str(log_dir), "error": str(error)})

    if not dry_run and deleted_job_ids:
        with STORE_LOCK:
            for job_id in deleted_job_ids:
                current = JOBS.get(job_id)
                if current and current.get("status") in {"done", "failed"}:
                    JOBS.pop(job_id, None)

    return {
        "ok": not errors,
        "dry_run": dry_run,
        "deleted_job_count": len(deleted_job_ids),
        "deleted_job_ids": deleted_job_ids,
        "deleted_frame_file_count": deleted_frame_files,
        "deleted_job_dir_count": deleted_job_dirs,
        "deleted_orphan_job_dir_count": deleted_orphan_job_dirs,
        "deleted_extra_dir_count": deleted_extra_dirs,
        "deleted_extra_file_count": deleted_extra_files,
        "deleted_log_dir_count": deleted_log_dirs,
        "skipped_active_job_count": len(skipped),
        "skipped_active_job_ids": [str(job.get("job_id")) for job in skipped],
        "freed_bytes": freed_bytes,
        "errors": errors,
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


def _is_within(path: Path, parent: Path) -> bool:
    """path가 parent 폴더 안에 있는지 확인합니다."""
    try:
        path.resolve().relative_to(parent)
        return True
    except (OSError, ValueError):
        return False


def _path_size(path: Path) -> int:
    """파일 또는 폴더의 전체 크기를 byte 단위로 계산합니다."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _delete_path(path: Path, dry_run: bool) -> int:
    """파일/폴더 크기를 계산하고 dry_run이 아니면 실제로 삭제합니다."""
    size = _path_size(path)
    if not dry_run:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return size
