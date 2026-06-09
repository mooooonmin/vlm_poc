# 테스트 결과 기록

이 문서는 사람이 확인한 검증 결과만 관리합니다. 실행 방법과 구조 설명은 루트 `README.md`를 기준으로 봅니다.
자동 생성 상세 로그는 `logs/evaluation/*`, `logs/timeslicing/*`에 저장되며, 임시파일 정리 대상입니다.

## 기준 환경

| 항목 | 확인 결과 | 근거 |
| --- | --- | --- |
| GPU | NVIDIA GeForce RTX 4070 Ti | `nvidia-smi` |
| Docker | Docker Desktop Linux Engine 응답 | `docker version` |
| vLLM 컨테이너 | `vlm-vllm-qwen`, `vllm/vllm-openai:latest`, port `8000` | `docker ps`, `/api/vllm-status` |
| vLLM 모델 | `Qwen/Qwen3-VL-2B-Instruct`, `max_model_len=8192` | `GET http://localhost:8000/v1/models` |
| Kubernetes | 로컬 context 미설정 또는 검증 불가 | `kubectl config current-context`, `/api/timeslicing/logs` |

현재 검증 범위는 로컬 Windows/Docker/vLLM 단일 worker입니다. Kubernetes time-slicing 실검증은 아직 완료되지 않았습니다.

## 날짜별 테스트 기록

| 일시 | 환경 | 입력 영상 | 샘플 프레임 수 | 처리 결과 | 실패 원인 | 비고 |
| --- | --- | --- | ---: | --- | --- | --- |
| 2026-06-08 | 로컬 Docker/vLLM | 모델 API | - | 성공 | - | `/v1/models`에서 `Qwen/Qwen3-VL-2B-Instruct` 응답 |
| 2026-06-08 | 로컬 UI/API | 영상 batch 입력 | 6 | 성공 | - | `/api/jobs/video-batch`, 최대 3개 입력 슬롯 확인 |
| 2026-06-08 | 로컬 프롬프트 | synthetic 시간 질문 | 6 | 성공 | - | `답변: 약 1.50초` 회귀 검증 |
| 2026-06-08 | 로컬 프롬프트 | synthetic 영상 종류 질문 | 6 | 성공 | - | 불확실한 경우 `확인 불가` 처리 확인 |
| 2026-06-08 | 로컬 UI/API | 원본 JSON 저장 위치 | - | 성공 | - | 화면에서는 제거, `tmp/jobs/{job_id}/job.json`의 `raw` 필드에 저장 |
| 2026-06-08 | 로컬 정적 검증 | 프롬프트 모듈 분리 | - | 성공 | - | `prompt_utils.py` 분리 후 정적 검증 |
| 2026-06-08 | 로컬 정적 검증 | 주석 보강 | - | 성공 | - | `app.py`, `static/app.js`, `evaluation_runner.py`, `prompt_utils.py` 보강 |
| 2026-06-08 | 로컬 API | 임시파일 정리 | - | 성공 | - | `POST /api/tmp/cleanup?dry_run=true`로 정리 대상 확인 |
| 2026-06-08 | 로컬 API | 생성 로그 정리 | - | 성공 | - | `logs/evaluation/*`, `logs/timeslicing/*`가 dry-run 대상에 포함됨 |
| 2026-06-08 | 로컬 UI | 대시보드 레이아웃 | - | 성공 | - | 1365x768 headless screenshot 기준 3열 대시보드 확인 |

## 최근 정리 dry-run

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
