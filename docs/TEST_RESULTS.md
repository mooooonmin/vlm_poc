# 테스트 결과 기록

이 문서는 검증 결과만 관리합니다. 실행 방법과 구조 설명은 루트 `README.md`를 기준으로 봅니다.

## 로컬 환경

| 항목 | 확인 결과 | 근거 |
| --- | --- | --- |
| GPU | NVIDIA GeForce RTX 4070 Ti | `nvidia-smi` |
| Docker | Docker Desktop Linux Engine 응답 | `docker version` |
| vLLM 컨테이너 | `vlm-vllm-qwen`, `vllm/vllm-openai:latest`, port `8000` | `docker ps`, `/api/vllm-status` |
| vLLM 모델 | `Qwen/Qwen3-VL-2B-Instruct`, `max_model_len=8192` | `GET http://localhost:8000/v1/models` |
| Kubernetes | 로컬 context 미설정 또는 검증 불가 | `kubectl config current-context`, `/api/timeslicing/logs` |

Kubernetes time-slicing 실검증은 아직 완료되지 않았습니다. 현재 검증 범위는 로컬 Windows/Docker/vLLM 단일 worker입니다.

## 기능 검증 요약

| 일시 | 항목 | 결과 | 근거 |
| --- | --- | --- | --- |
| 2026-06-08 | vLLM 모델 API | 성공 | `/v1/models`에서 `Qwen/Qwen3-VL-2B-Instruct` 응답 |
| 2026-06-08 | 영상 batch 생성 | 성공 | `/api/jobs/video-batch`, 최대 3개 입력 슬롯 |
| 2026-06-08 | 질문 유형별 프롬프트 | 성공 | 시간 질문 `답변: 약 1.50초`, 영상 종류 질문 `답변: 확인 불가` 확인 |
| 2026-06-08 | 원본 JSON 저장 위치 | 성공 | 화면에서는 제거, `tmp/jobs/{job_id}/job.json`의 `raw` 필드에 저장 |
| 2026-06-08 | 프롬프트 모듈 분리 | 성공 | `prompt_utils.py` 분리 후 synthetic 시간 질문 회귀 검증 |
| 2026-06-08 | 주석 보강 | 성공 | `app.py`, `static/app.js`, `evaluation_runner.py`, `prompt_utils.py` 보강 후 정적 검증 |
| 2026-06-08 | 임시파일 정리 버튼 | 성공 | `POST /api/tmp/cleanup?dry_run=true`로 정리 대상 확인 |
| 2026-06-08 | 임시파일 정리 범위 확장 | 성공 | 고아 job 폴더, 고아 프레임, 평가/검증 폴더, layout 이미지가 dry-run 대상에 포함됨 |
| 2026-06-08 | 생성 로그 정리 범위 확장 | 성공 | `logs/evaluation/*`, `logs/timeslicing/*`가 dry-run 대상에 포함됨 |
| 2026-06-08 | UI 레이아웃 | 성공 | 1365x768 headless screenshot 기준 3열 대시보드와 정리 버튼 표시 확인 |

## 최근 확인한 정리 대상

`POST /api/tmp/cleanup?dry_run=true` 기준입니다. 실제 삭제는 수행하지 않았습니다.

| 항목 | 개수 |
| --- | ---: |
| 고아 job 폴더 | 0 |
| 고아 프레임 | 0 |
| 평가/검증 폴더 | 0 |
| layout 이미지 | 0 |
| 생성 로그 폴더 | 9 |
| 예상 정리 용량 | 173,235 bytes |

## 검증 명령

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py job_store.py prompt_utils.py runtime_utils.py worker_registry.py video_utils.py evaluation_runner.py
node --check static\app.js
git diff --check
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/config
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/workers/refresh -Method Post
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
Invoke-RestMethod -Uri http://localhost:8000/v1/models
```

## 남은 실제 테스트

| 항목 | 목적 |
| --- | --- |
| 실제 mp4 3~5개 | synthetic 영상이 아닌 실제 영상 품질 확인 |
| 공개 YouTube URL 3~5개 | `yt-dlp` 다운로드 안정성 확인 |
| frame count `4`, `6`, `8` 비교 | 정확도와 처리시간 균형 확인 |
| 긴 영상 입력 | 영상 길이 제한과 샘플링 안정성 확인 |
| Linux/Kubernetes GPU node | time-slicing 실제 적용 확인 |
