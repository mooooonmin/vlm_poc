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
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtube.com/shorts/7WQZxMdXbqI` | 4 | 성공 | - | job `1780964816_b92b7798`, 7,206ms, 터널/트럭 장면 요약 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtube.com/shorts/7WQZxMdXbqI` | 6 | 성공 | - | job `1780964816_05cecafb`, 4,414ms, 일부 `확인 불가` 포함 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtu.be/kSW4PKuowYg` | 4 | 성공 | - | job `1780964816_265dde12`, 10,050ms, 응답 반복 문장 발생 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtu.be/kSW4PKuowYg` | 6 | 성공 | - | job `1780964816_249120af`, 8,168ms, 차량/사람 장면 요약 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://www.youtube.com/watch?v=jNQXAC9IVRw` | 4 | 성공 | - | job `1780964816_66484633`, 4,581ms, 인물 장면 요약 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://www.youtube.com/watch?v=jNQXAC9IVRw` | 6 | 성공 | - | job `1780964816_61d87cdc`, 3,353ms, 일부 `확인 불가` 포함 |
| 2026-06-09 | 로컬 API | 존재하지 않는 YouTube URL | 4 | 실패 처리 성공 | YouTube 다운로드 실패 | job `1780965058_600a40c3`, 사용자용 실패 메시지 표시 확인 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtu.be/kSW4PKuowYg` | 4 | 성공 | - | job `1780965257_abe5a85b`, 10,188ms, 반복 문장 억제 보정 후 재검증 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtube.com/shorts/7WQZxMdXbqI` | 8 | 성공 | - | job `1780966150_94cec3be`, 9,641ms, 추정성 표현과 반복 관찰 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://youtu.be/kSW4PKuowYg` | 8 | 성공 | - | job `1780966150_88571e75`, 9,479ms, 차량/사람 장면 요약 |
| 2026-06-09 | 로컬 Docker/vLLM | `https://www.youtube.com/watch?v=jNQXAC9IVRw` | 8 | 성공 | - | job `1780966150_599f6560`, 10,356ms, 일부 `확인 불가` 포함 |

## 2026-06-09 실제 영상 테스트 메모

| 항목 | 결과 |
| --- | --- |
| 로컬 mp4 | 저장소 안에서 테스트 가능한 mp4 파일을 찾지 못해 미수행 |
| 공개 YouTube URL | 3개 URL, `4`, `6`, `8` 프레임 조건, 총 9개 job 모두 성공 |
| 비교한 프레임 수 | `4`, `6`, `8` |
| 관찰된 품질 이슈 | `8` 프레임 조건에서도 일부 응답에서 추정성 표현, 반복 문장, `확인 불가` 혼합 발생 |
| 적용한 보정 | 반복 구절과 메타 표현 억제, 사용자용 실패 메시지 저장 |
| 다음 보정 방향 | 근거 프레임 표기 방식 개선, 실제 mp4 테스트 데이터 확보 |

## 최근 정리 dry-run

`POST /api/tmp/cleanup?dry_run=true` 기준입니다. 실제 삭제는 수행하지 않았습니다.

| 항목 | 개수 |
| --- | ---: |
| 완료/실패 job 폴더 | 12 |
| job 연결 프레임 | 62 |
| 고아 job 폴더 | 0 |
| 고아 프레임 | 0 |
| 평가/검증 폴더 | 0 |
| layout 이미지 | 0 |
| 생성 로그 폴더 | 0 |
| 예상 정리 용량 | 25,117,290 bytes |

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
| 실제 mp4 3~5개 | 테스트 영상 파일 확보 후 품질 확인 |
| 공개 YouTube URL 3~5개 | 3개 URL은 `4`, `6`, `8` 프레임 조건으로 1차 확인 완료 |
| frame count `4`, `6`, `8` 비교 | 1차 확인 완료. `8`은 처리시간이 늘고 품질이 항상 개선되지는 않음 |
| 긴 영상 입력 | 영상 길이 제한과 샘플링 안정성 확인 |
| Linux/Kubernetes GPU node | time-slicing 실제 적용 확인 |
