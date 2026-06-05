"""
영상 저장, 다운로드, 프레임 샘플링 유틸리티.

PoC 단계에서는 원본 영상을 모델에 직접 넣지 않고, 균등 추출한 JPEG 프레임들을
멀티 이미지 입력으로 vLLM에 전달합니다.
"""

from __future__ import annotations

import base64
import mimetypes
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import cv2
import requests
from fastapi import UploadFile
from yt_dlp import YoutubeDL


DEFAULT_FRAME_COUNT = 6


@dataclass
class SampledFrame:
    """추출된 프레임 파일과 영상 내 위치 정보를 담습니다."""

    index: int
    timestamp_sec: float
    path: Path


@dataclass
class SampleResult:
    """영상 메타데이터와 추출 프레임 목록을 담습니다."""

    fps: float
    total_frames: int
    duration_sec: float
    frames: list[SampledFrame]


def create_job_dir(base_dir: Path) -> Path:
    """요청마다 격리된 임시 작업 폴더를 생성합니다."""
    job_dir = base_dir / "jobs" / f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


async def save_upload_file(upload: UploadFile, job_dir: Path, max_bytes: int | None = None) -> Path:
    """업로드된 영상을 작업 폴더에 저장합니다."""
    suffix = Path(upload.filename or "upload.mp4").suffix or ".mp4"
    output_path = job_dir / f"input{suffix}"
    written = 0
    with output_path.open("wb") as file:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if max_bytes is not None and written > max_bytes:
                output_path.unlink(missing_ok=True)
                raise ValueError(f"업로드 파일이 PoC 제한을 초과했습니다. 제한: {max_bytes} bytes")
            file.write(chunk)
    return output_path


def download_video(url: str, job_dir: Path) -> Path:
    """
    영상 URL을 다운로드해 작업 폴더에 저장합니다.

    중요한 차이:
    - `https://example.com/sample.mp4`처럼 실제 mp4 파일을 직접 가리키는 URL은 requests로 바로 받을 수 있습니다.
    - `https://youtu.be/...`, `https://www.youtube.com/watch?...` 같은 URL은 영상 파일이 아니라 웹 페이지/스트리밍 서비스 URL입니다.
      이 경우 requests로 저장하면 HTML이나 스트리밍 메타데이터가 저장될 수 있고, OpenCV는 그 파일을 영상으로 열 수 없습니다.
    - 그래서 YouTube 계열 URL은 yt-dlp를 사용해 실제 영상 파일을 내려받습니다.

    주의:
    - yt-dlp도 모든 공개 URL을 항상 다운로드할 수 있는 것은 아닙니다.
      연령 제한, 지역 제한, 로그인 필요, 플랫폼 차단, 네트워크 제한이 있으면 실패할 수 있습니다.
    - 테스트에는 본인이 사용할 권한이 있는 공개 영상 또는 직접 소유한 영상을 사용해야 합니다.
    """
    if _is_youtube_url(url):
        return _download_video_with_ytdlp(url, job_dir)

    return _download_direct_video(url, job_dir)


def _download_direct_video(url: str, job_dir: Path) -> Path:
    """
    실제 영상 파일을 직접 가리키는 URL을 requests로 다운로드합니다.

    예시:
    - https://example.com/sample.mp4
    - https://example.com/cctv_test.mov

    이 방식은 URL 응답 본문이 곧 영상 파일이어야 합니다.
    YouTube 짧은 링크처럼 웹 페이지를 반환하는 URL에는 맞지 않습니다.
    """
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix or ".mp4"
    output_path = job_dir / f"input{suffix}"
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with output_path.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)
    return output_path


def _download_video_with_ytdlp(url: str, job_dir: Path) -> Path:
    """
    YouTube 등 플랫폼 URL을 yt-dlp로 다운로드합니다.

    yt-dlp를 쓰는 이유:
    - YouTube URL은 단일 mp4 파일 주소가 아니라 플레이어 페이지/스트리밍 리소스입니다.
    - yt-dlp는 해당 페이지에서 실제 다운로드 가능한 영상 스트림 정보를 찾아 파일로 저장해 줍니다.
    - OpenCV는 최종적으로 저장된 로컬 영상 파일을 열어 프레임을 추출합니다.

    포맷 선택:
    - `best[ext=mp4]/best`는 mp4가 있으면 mp4를 우선 선택하고, 없으면 사용 가능한 최선 포맷을 받습니다.
    - PoC에서는 분석용 프레임 추출이 목적이므로 최고 화질보다 OpenCV 호환성과 다운로드 성공률을 우선합니다.
    """
    output_template = str(job_dir / "input.%(ext)s")
    options = {
        "outtmpl": output_template,
        "format": "best[ext=mp4]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    except Exception as error:
        raise ValueError(f"yt-dlp로 영상 다운로드에 실패했습니다: {error}") from error

    # yt-dlp는 최종 파일명을 info dict로 알려줍니다. 이 경로가 있으면 그대로 사용합니다.
    downloaded = Path(downloader.prepare_filename(info))
    if downloaded.exists():
        return downloaded

    # 일부 포맷에서는 확장자가 예상과 다를 수 있어 작업 폴더의 input.* 파일을 다시 찾습니다.
    candidates = sorted(job_dir.glob("input.*"))
    if candidates:
        return candidates[0]

    raise ValueError("yt-dlp 다운로드는 완료됐지만 저장된 영상 파일을 찾을 수 없습니다.")


def _is_youtube_url(url: str) -> bool:
    """URL 호스트가 YouTube 계열인지 확인합니다."""
    host = (urlparse(url).hostname or "").lower()
    return host in {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def sample_video_frames(video_path: Path, output_dir: Path, frame_count: int) -> SampleResult:
    """OpenCV로 영상을 열고 전체 구간에서 프레임을 균등 추출합니다."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"영상을 열 수 없습니다: {video_path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total_frames <= 0:
        capture.release()
        raise ValueError("영상 프레임 수를 확인할 수 없습니다.")

    duration_sec = total_frames / fps if fps > 0 else 0
    indices = _uniform_indices(total_frames, frame_count)
    job_prefix = f"{video_path.parent.name}_{uuid.uuid4().hex[:6]}"

    sampled: list[SampledFrame] = []
    for order, frame_index in enumerate(indices, start=1):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok:
            continue

        frame_path = output_dir / f"{job_prefix}_{order:02d}.jpg"
        cv2.imwrite(str(frame_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        timestamp_sec = frame_index / fps if fps > 0 else 0
        sampled.append(SampledFrame(index=order, timestamp_sec=timestamp_sec, path=frame_path))

    capture.release()
    if not sampled:
        raise ValueError("추출된 프레임이 없습니다.")

    return SampleResult(
        fps=fps,
        total_frames=total_frames,
        duration_sec=duration_sec,
        frames=sampled,
    )


def encode_frame_to_data_url(frame_path: Path) -> str:
    """프레임 JPEG를 vLLM image_url에 넣을 수 있는 base64 data URL로 변환합니다."""
    mime_type = mimetypes.guess_type(frame_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(frame_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _uniform_indices(total_frames: int, frame_count: int) -> list[int]:
    """영상 전체 구간에서 중복을 줄이며 균등한 프레임 인덱스를 계산합니다."""
    count = max(1, min(frame_count, total_frames))
    if count == 1:
        return [max(0, total_frames // 2)]

    last_index = total_frames - 1
    indices = [round(i * last_index / (count - 1)) for i in range(count)]
    return sorted(set(int(index) for index in indices))
