# 영상 입력 VLM 분석 PoC

영상 파일 또는 YouTube URL을 입력하면 프레임을 균등 샘플링하고, 샘플 프레임을 `Qwen/Qwen3-VL-2B-Instruct`에 전달해 한국어 분석 결과를 받는 PoC입니다.

## 현재 범위

| 항목 | 상태 |
| --- | --- |
| CUDA/GPU 확인 | `nvidia-smi` 기반 상태 확인 |
| vLLM 서빙 | Docker `vllm/vllm-openai:latest` 컨테이너 사용 |
| 영상 입력 | 파일 업로드, 직접 영상 URL, YouTube URL |
| 분석 방식 | 영상 전체를 직접 넣지 않고 균등 샘플 프레임을 멀티 이미지로 전달 |
| 다중 입력 | 화면에서 최대 3개 영상 batch 생성 |
| worker 분산 | `VLLM_WORKERS` 환경변수 기반 endpoint 분산 준비 |
| time-slicing | 로컬 Windows에서는 미적용. Linux/Kubernetes 검증 초안은 `k8s/` 참고 |
| 임시파일 정리 | 화면의 `임시파일 정리` 버튼 또는 `POST /api/tmp/cleanup` |

## 실행

Windows PowerShell:

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
docker pull vllm/vllm-openai:latest
python app.py
```

기본 접속 주소:

```text
http://127.0.0.1:8080
```

8080 포트가 사용 중이면 앱이 다음 빈 포트를 자동으로 찾아 실행합니다. 실제 주소는 `python app.py` 콘솔 출력에서 확인합니다.

## 기본 설정

| 항목 | 기본값 |
| --- | --- |
| 모델 | `Qwen/Qwen3-VL-2B-Instruct` |
| vLLM 포트 | `8000` |
| FastAPI 포트 | `8080` |
| 샘플 프레임 수 | 기본 `6`, 허용 `1~12` |
| 최대 토큰 | 기본 `512`, 허용 `64~2048` |
| 최대 batch 영상 수 | `3` |
| 업로드 파일 제한 | `1GB` |
| 영상 길이 제한 | `1800초` |
| `GPU_MEMORY_UTILIZATION` | `0.85` |
| `MAX_MODEL_LEN` | `8192` |
| vLLM 컨테이너 | `vlm-vllm-qwen` |

RTX 4070 Ti에서 OOM이 발생하면 샘플 프레임 수를 `4`로 낮추고, `MAX_MODEL_LEN=4096`으로 다시 테스트합니다.

## 주요 API

| Method | Endpoint | 용도 |
| --- | --- | --- |
| `GET` | `/api/gpu-status` | GPU 상태 확인 |
| `GET` | `/api/vllm-status` | vLLM 컨테이너/API 상태 확인 |
| `POST` | `/api/start-vllm` | vLLM 컨테이너 시작 |
| `POST` | `/api/stop-vllm` | vLLM 컨테이너 종료 |
| `POST` | `/api/jobs/video-batch` | 영상 1~3개 분석 batch 생성 |
| `GET` | `/api/batches/{batch_id}` | batch 진행률 조회 |
| `GET` | `/api/jobs/{job_id}` | 단일 job 결과 조회 |
| `GET` | `/api/jobs/stats` | 최근 job 통계 조회 |
| `POST` | `/api/tmp/cleanup` | 완료/실패 job과 tmp 테스트 산출물 정리 |
| `GET` | `/api/timeslicing` | time-slicing 안내 조회 |
| `POST` | `/api/timeslicing/logs` | K8s 검증 로그 수집 |

`POST /api/tmp/cleanup?dry_run=true`를 사용하면 실제 삭제 없이 정리 대상만 확인합니다.

## 주요 파일

| 파일/폴더 | 역할 |
| --- | --- |
| `app.py` | FastAPI 서버, job dispatcher, API 라우트 |
| `prompt_utils.py` | 질문 유형 분류, vLLM payload 생성, 응답 후처리 |
| `video_utils.py` | 영상 저장/다운로드, 프레임 샘플링, base64 변환 |
| `job_store.py` | job 상태 저장, `job.json` 기록, 임시파일 정리 |
| `runtime_utils.py` | CUDA, Docker vLLM, time-slicing 로그 유틸 |
| `worker_registry.py` | vLLM worker endpoint 상태와 배정 관리 |
| `evaluation_runner.py` | 반복 평가 리포트 생성 |
| `templates/`, `static/` | 테스트 화면 |
| `k8s/` | Kubernetes time-slicing/vLLM 배포 초안 |
| `docs/TEST_RESULTS.md` | 검증 결과 기록 |

## 저장 파일

| 경로 | 내용 |
| --- | --- |
| `tmp/jobs/{job_id}/` | 업로드/다운로드 영상, `job.json` |
| `tmp/frames/` | 화면 미리보기와 vLLM 요청에 사용한 추출 프레임 |
| `logs/evaluation/{run_id}/` | 평가 러너 리포트 |
| `logs/timeslicing/{run_id}/` | Kubernetes/time-slicing 검증 리포트 |

`임시파일 정리`는 완료/실패 job, 고아 프레임, `tmp/evaluation_samples`, `tmp/validation`, `tmp/layout_*.png`를 삭제합니다. 진행 중인 `queued/running` job은 삭제하지 않습니다.

## 모델과 라이선스

기본 모델은 `Qwen/Qwen3-VL-2B-Instruct`입니다. 모델 카드 기준 라이선스는 `apache-2.0`이고, VLM 태스크와 vLLM 실행 예시가 제공됩니다.

출처: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct

## 관련 문서

| 문서 | 내용 |
| --- | --- |
| `docs/TEST_RESULTS.md` | 로컬 검증 결과 |
| `k8s/README.md` | Linux/Kubernetes time-slicing 검증 절차 |

참고 출처:
- vLLM Docker 문서: https://docs.vllm.ai/en/stable/deployment/docker.html
- NVIDIA k8s-device-plugin: https://github.com/NVIDIA/k8s-device-plugin
