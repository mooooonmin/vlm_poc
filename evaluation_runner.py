#!/usr/bin/env python3
"""
영상 분석 PoC 평가 러너입니다.

목적:
- 여러 영상 샘플을 순차로 분석합니다.
- 각 job이 done/failed가 될 때까지 polling합니다.
- 결과를 logs/evaluation/{run_id}/summary.json, summary.md로 저장합니다.

사용 예:
- synthetic 샘플 1개로 빠른 검증:
  .venv\\Scripts\\python.exe evaluation_runner.py --synthetic-count 1
- 파일/URL 샘플 목록으로 검증:
  .venv\\Scripts\\python.exe evaluation_runner.py --samples samples.json

samples.json 형식:
[
  {"name": "local mp4", "type": "file", "path": "D:/videos/test.mp4"},
  {"name": "youtube", "type": "url", "url": "https://youtu.be/..."}
]
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app import DEFAULT_MODEL_ID, DEFAULT_VLLM_ENDPOINT, TMP_DIR, enqueue_analysis_job
from job_store import create_job, get_job


DEFAULT_PROMPT = (
    "이 영상에서 발생한 주요 상황을 시간 순서대로 한국어로만 요약해줘. "
    "다른 언어는 사용하지 말고, 보이는 내용만 근거로 작성해줘."
)


def main() -> None:
    args = parse_args()
    samples = load_samples(args)
    if not samples:
        raise SystemExit("평가할 샘플이 없습니다. --samples 또는 --synthetic-count를 지정하세요.")

    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log_dir = Path("logs") / "evaluation" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_text()
    results = []
    for index, sample in enumerate(samples, start=1):
        print(f"[{index}/{len(samples)}] {sample['name']} 분석 시작")
        result = run_sample(sample, args.frame_count, args.max_tokens, args.timeout_sec)
        results.append(result)
        print(f"  -> {result['status']} / {result.get('duration_ms')}ms / {result.get('job_id')}")

    report = build_report(run_id, started_at, now_text(), samples, results, log_dir)
    (log_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (log_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"summary_json={log_dir / 'summary.json'}")
    print(f"summary_md={log_dir / 'summary.md'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="영상 분석 PoC 평가 러너")
    parser.add_argument("--samples", help="평가 샘플 JSON 파일 경로")
    parser.add_argument("--synthetic-count", type=int, default=0, help="자동 생성 synthetic mp4 샘플 개수")
    parser.add_argument("--frame-count", type=int, default=1, help="샘플당 추출 프레임 수")
    parser.add_argument("--max-tokens", type=int, default=128, help="vLLM 응답 최대 토큰")
    parser.add_argument("--timeout-sec", type=int, default=300, help="job 1개 최대 대기 시간")
    return parser.parse_args()


def load_samples(args: argparse.Namespace) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    if args.samples:
        samples.extend(json.loads(Path(args.samples).read_text(encoding="utf-8")))
    if args.synthetic_count:
        samples.extend(create_synthetic_samples(args.synthetic_count))
    return samples


def create_synthetic_samples(count: int) -> list[dict[str, Any]]:
    video_dir = TMP_DIR / "evaluation_samples"
    video_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for sample_index in range(count):
        video_path = video_dir / f"synthetic_eval_{sample_index + 1}.mp4"
        write_synthetic_video(video_path, sample_index)
        samples.append({"name": f"synthetic_eval_{sample_index + 1}", "type": "file", "path": str(video_path)})
    return samples


def write_synthetic_video(video_path: Path, sample_index: int) -> None:
    """평가 러너 자체 검증용 짧은 mp4를 생성합니다."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 2.0, (224, 224))
    for frame_index in range(4):
        frame = np.zeros((224, 224, 3), dtype=np.uint8)
        frame[:] = (30 + sample_index * 20, 100 + frame_index * 10, 210)
        cv2.putText(
            frame,
            f"EVAL {sample_index + 1}-{frame_index}",
            (28, 112),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
        )
        writer.write(frame)
    writer.release()


def run_sample(sample: dict[str, Any], frame_count: int, max_tokens: int, timeout_sec: int) -> dict[str, Any]:
    source = build_source(sample)
    job = create_job(
        TMP_DIR,
        source=source,
        settings={
            "frame_count": frame_count,
            "max_tokens": max_tokens,
            "model_id": DEFAULT_MODEL_ID,
            "endpoint": DEFAULT_VLLM_ENDPOINT,
            "prompt": sample.get("prompt") or DEFAULT_PROMPT,
        },
    )
    enqueue_analysis_job(job["job_id"])
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        current = get_job(job["job_id"])
        if current and current.get("status") in {"done", "failed"}:
            return summarize_job(sample, current)
        time.sleep(2)

    current = get_job(job["job_id"]) or job
    return {
        "sample": sample,
        "job_id": job["job_id"],
        "status": "timeout",
        "message": f"{timeout_sec}초 안에 job이 끝나지 않았습니다.",
        "last_job": current,
    }


def build_source(sample: dict[str, Any]) -> dict[str, Any]:
    sample_type = sample.get("type")
    if sample_type == "file":
        path = Path(sample["path"]).resolve()
        return {"type": "upload", "name": sample.get("name") or path.name, "path": str(path), "size_bytes": path.stat().st_size}
    if sample_type == "url":
        return {"type": "url", "name": sample.get("name") or sample["url"], "url": sample["url"]}
    raise ValueError(f"지원하지 않는 sample type입니다: {sample_type}")


def summarize_job(sample: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample": sample,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "message": job.get("message"),
        "worker_id": job.get("worker_id"),
        "duration_ms": job.get("duration_ms"),
        "frame_extract_duration_ms": job.get("frame_extract_duration_ms"),
        "vllm_duration_ms": job.get("vllm_duration_ms"),
        "korean_check": job.get("korean_check"),
        "korean_retry_used": job.get("korean_retry_used"),
        "korean_repair_used": job.get("korean_repair_used"),
        "korean_fallback_used": job.get("korean_fallback_used"),
        "loop_checks": job.get("loop_checks"),
        "failure_stage": job.get("failure_stage"),
        "failure_reason": job.get("failure_reason"),
        "answer_preview": (job.get("answer") or "")[:500],
    }


def build_report(
    run_id: str,
    started_at: str,
    finished_at: str,
    samples: list[dict[str, Any]],
    results: list[dict[str, Any]],
    log_dir: Path,
) -> dict[str, Any]:
    success_count = sum(1 for result in results if result.get("status") == "done")
    failed_count = sum(1 for result in results if result.get("status") == "failed")
    timeout_count = sum(1 for result in results if result.get("status") == "timeout")
    durations = [result["duration_ms"] for result in results if isinstance(result.get("duration_ms"), (int, float))]
    korean_fallback_count = sum(1 for result in results if result.get("korean_fallback_used"))
    korean_fallback_rate = round(korean_fallback_count / len(samples), 3) if samples else 0
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "log_dir": str(log_dir),
        "sample_count": len(samples),
        "success_count": success_count,
        "failed_count": failed_count,
        "timeout_count": timeout_count,
        "success_rate": round(success_count / len(samples), 3) if samples else 0,
        "average_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "korean_fallback_count": korean_fallback_count,
        "korean_fallback_rate": korean_fallback_rate,
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Evaluation Summary",
        "",
        f"- run_id: `{report['run_id']}`",
        f"- sample_count: `{report['sample_count']}`",
        f"- success_rate: `{report['success_rate']}`",
        f"- average_duration_ms: `{report['average_duration_ms']}`",
        f"- korean_fallback_count: `{report['korean_fallback_count']}`",
        f"- korean_fallback_rate: `{report['korean_fallback_rate']}`",
        "",
        "| 상태 | 샘플 | job_id | worker | 전체 ms | vLLM ms | 한국어 |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for result in report["results"]:
        sample_name = result.get("sample", {}).get("name", "-")
        korean = result.get("korean_check") or {}
        lines.append(
            f"| `{result.get('status')}` | {sample_name} | `{result.get('job_id')}` | "
            f"{result.get('worker_id') or '-'} | {result.get('duration_ms') or '-'} | "
            f"{result.get('vllm_duration_ms') or '-'} | {korean.get('ok', '-')} |"
        )
    lines.append("")
    return "\n".join(lines)


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
