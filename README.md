# 영상 입력 VLM 분석 PoC

영상 파일 또는 YouTube URL을 입력하면 프레임을 균등 샘플링하고, 샘플 프레임을 `Qwen/Qwen3-VL-2B-Instruct`에 전달해 한국어 분석 결과를 받는 임시 PoC입니다.

## 목표

- RTX 4070 Ti 환경에서 CUDA/GPU 사용 가능 여부를 확인합니다.
- vLLM Docker 컨테이너로 Qwen VLM을 OpenAI 호환 API 형태로 서빙합니다.
- FastAPI 화면에서 영상 입력, 분석 job 생성, 프레임 미리보기, VLM 응답 확인을 테스트합니다.
- Kubernetes time-slicing은 로컬 Windows/Docker에서 직접 적용하지 않고, 추후 Linux/K8s GPU 노드에서 여러 vLLM worker를 띄워 검증합니다.

## 모델 기준

기본 모델:

- `Qwen/Qwen3-VL-2B-Instruct`
- 라이선스 근거: Hugging Face 모델 카드의 `License: apache-2.0`
- VLM 근거: Hugging Face 태스크 `Image-Text-to-Text`
- vLLM 근거: 모델 카드의 `vllm serve "Qwen/Qwen3-VL-2B-Instruct"` 예시
- 출처: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct

대체 후보:

- `Qwen/Qwen3-VL-4B-Instruct-FP8`
- `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`

## 실행 순서

Windows PowerShell:

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
docker pull vllm/vllm-openai:latest
python app.py
```

`docker pull vllm/vllm-openai:latest`는 필수는 아니지만 첫 실행 전에 권장합니다.
앱의 `vLLM 시작 / GPU 점유` 버튼도 내부적으로 같은 이미지를 자동 다운로드하지만, vLLM Docker 이미지는 수십 GB 크기라 첫 실행에서 오래 대기할 수 있습니다.
이미지를 미리 받아두면 화면에서 vLLM을 시작할 때 Docker 이미지 다운로드 단계는 건너뛰고 컨테이너 시작과 Qwen 모델 다운로드/로딩 단계로 바로 넘어갈 수 있습니다.

이미지가 이미 받아졌는지 확인하려면 다음 명령을 사용합니다.

```powershell
docker images vllm/vllm-openai
```

기본 접속 주소:

```text
http://127.0.0.1:8080
```

8080 포트가 사용 중이면 앱이 8081, 8082처럼 다음 빈 포트를 자동으로 찾아 실행합니다. 실제 접속 주소는 `python app.py` 콘솔 출력에서 확인합니다.

## Python API

| Method | Endpoint | 용도 |
| --- | --- | --- |
| `GET` | `/api/gpu-status` | `nvidia-smi` 기반 GPU/CUDA 상태 확인 |
| `GET` | `/api/vllm-status` | Docker 컨테이너와 `http://localhost:8000/v1/models` 기준 vLLM 상태 확인 |
| `GET` | `/api/vllm/logs` | vLLM 컨테이너 로그 tail 조회 |
| `POST` | `/api/start-vllm` | Python에서 Docker 기반 vLLM 컨테이너 시작 |
| `POST` | `/api/stop-vllm` | vLLM 컨테이너 종료 및 GPU 점유 해제 |
| `GET` | `/api/workers` | 등록된 vLLM worker endpoint와 배정 상태 조회 |
| `POST` | `/api/workers/refresh` | 각 worker의 `/v1/models` readiness 확인 |
| `POST` | `/api/jobs/video` | 영상 분석 job 생성. 즉시 `job_id` 반환 |
| `GET` | `/api/jobs/{job_id}` | 분석 상태, 추출 프레임, 결과, 에러 조회 |
| `GET` | `/api/jobs` | 최근 분석 job 목록 조회 |
| `GET` | `/api/jobs/stats` | 최근 job 성공/실패/처리시간/worker별 요약 조회 |
| `POST` | `/api/analyze-video` | 기존 호환용 API. 내부적으로 job을 생성하고 `job_id` 반환 |
| `GET` | `/api/timeslicing` | K8s time-slicing 초안과 주의사항 확인 |
| `POST` | `/api/timeslicing/logs` | K8s/time-slicing 검증용 명령 결과를 `logs/timeslicing/...`에 저장 |

## 주요 파일

| 파일/폴더 | 역할 |
| --- | --- |
| `app.py` | FastAPI 서버, 영상 분석 job dispatcher, API 라우트 |
| `job_store.py` | `job_id` 기반 분석 상태 저장과 `tmp/jobs/{job_id}/job.json` 기록 |
| `video_utils.py` | 영상 업로드 저장, YouTube/URL 다운로드, OpenCV 프레임 샘플링, base64 이미지 변환 |
| `runtime_utils.py` | CUDA 상태 확인, Docker 기반 vLLM 시작/종료/로그 확인, time-slicing 검증 로그 수집 |
| `worker_registry.py` | `VLLM_WORKERS` 기반 vLLM worker 목록, readiness, busy/ready 상태 관리 |
| `templates/index.html`, `static/app.js`, `static/style.css` | 테스트용 웹 화면 |
| `k8s/` | 추후 Linux/Kubernetes GPU 노드에서 검증할 time-slicing 및 vLLM 배포 manifest 초안 |

## 분석 처리 방식

- 기본 처리 방식은 단일 worker 순차 처리입니다.
- `VLLM_WORKERS` 환경변수에 여러 vLLM endpoint를 쉼표로 넣으면 dispatcher가 ready 상태 worker에 job을 배정합니다.
- 단일 RTX 4070 Ti에서 여러 VLM 요청을 동시에 보내면 VRAM 부족과 지연 원인을 구분하기 어려우므로 로컬 기본값은 worker 1개입니다.
- 각 분석 요청은 `job_id`를 받고, `tmp/jobs/{job_id}/job.json`에 상태와 결과를 저장합니다.
- job 결과에는 `worker_id`, `worker_endpoint`, `queued_at`, `started_at`, `finished_at`이 기록됩니다.
- 새 job 결과에는 `frame_extract_duration_ms`, `vllm_duration_ms`, `duration_ms`, `failure_stage`, `failure_reason`도 기록됩니다.
- 반복 테스트 루프는 `loop_checks`에 기록됩니다: `1_korean_response`, `2_real_video_stats`, `3_gpu_snapshot`, `4_worker_assignment`.
- 한국어 응답 품질은 `korean_check`에 한글 글자 수와 한글 비율로 기록하고, 기준 미달이면 기본 1회 재요청합니다. 재요청 여부는 `korean_retry_used`에 남습니다.
- 재요청 후에도 한국어가 아니면 원문 응답을 텍스트-only 요청으로 한국어 정리합니다. 이 복구 단계 사용 여부는 `korean_repair_used`에 남습니다.
- 복구 후에도 모델이 한국어를 따르지 않으면 앱이 한국어 경고문으로 원문 응답을 감싸서 표시합니다. 이 후처리 여부는 `korean_fallback_used`에 남습니다.
- GPU 상태는 job 단계별 `gpu_snapshots`에 저장합니다. 분석 시작, 프레임 추출 후, vLLM 요청 전후, 실패/완료 지점을 확인하는 용도입니다.
- 상태값은 `queued`, `running`, `done`, `failed`입니다.

다중 worker 설정 예시:

```powershell
$env:VLLM_WORKERS="http://localhost:8000/v1/chat/completions,http://localhost:8001/v1/chat/completions"
python app.py
```

중요: `VLLM_WORKERS`는 요청 분산 대상만 지정합니다. 실제 GPU 공유는 Kubernetes NVIDIA device-plugin time-slicing 또는 별도 GPU 배치 설정이 있어야 의미가 있습니다.

## 기본 설정

| 항목 | 기본값 |
| --- | --- |
| 모델 | `Qwen/Qwen3-VL-2B-Instruct` |
| vLLM 포트 | `8000` |
| FastAPI 화면 포트 | `8080` |
| 샘플 프레임 수 | 기본 `6`, 최대 `12` |
| `GPU_MEMORY_UTILIZATION` | `0.85` |
| `MAX_MODEL_LEN` | `8192` |
| 업로드 파일 제한 | 기본 `1GB` |
| 영상 길이 제한 | 기본 `1800초` |
| 컨테이너 이름 | `vlm-vllm-qwen` |
| worker 기본값 | `http://localhost:8000/v1/chat/completions` 1개 |
| `KOREAN_RETRY_ENABLED` | `1` |
| `KOREAN_MIN_HANGUL` | `5` |
| `KOREAN_MIN_RATIO` | `0.2` |

RTX 4070 Ti에서 OOM이 발생하면 샘플 프레임 수를 `4`로 낮추고, 환경 변수 `MAX_MODEL_LEN=4096`으로 낮춰 다시 테스트합니다.
평가 반복에서 한국어 fallback 비율이 높으면 먼저 실제 영상 샘플을 늘려 확인하고, 필요하면 `KOREAN_MIN_HANGUL`, `KOREAN_MIN_RATIO` 기준을 조정합니다.
synthetic 텍스트 영상처럼 화면의 영문 텍스트만 읽는 샘플은 fallback이 높게 나올 수 있으므로 실제 관제 영상과 구분해서 해석해야 합니다.

## YouTube URL 주의사항

YouTube URL은 직접 mp4 파일 주소가 아니므로 `yt-dlp`로 실제 영상 파일을 내려받은 뒤 OpenCV로 엽니다. 공개 영상이어도 비공개 전환, 연령 제한, 지역 제한, 로그인 필요, 네트워크 제한, YouTube 정책 변화에 따라 실패할 수 있습니다.

## Time-slicing 검증

로컬 Windows/Docker 테스트에서는 Kubernetes time-slicing을 실제 적용하지 않습니다. 실제 적용 절차는 `k8s/README.md`를 기준으로 진행합니다.
로컬에서 바로 검증 가능한 범위는 Docker GPU 기반 vLLM 단일 worker, worker endpoint 상태, job 로그/통계입니다.
Kubernetes time-slicing 실검증은 `kubectl` context가 연결된 Linux/K8s GPU node에서 수행해야 합니다.

## 로컬 평가 기록

| 일시 | run_id | 샘플 | 성공률 | 평균 처리시간 | 한국어 fallback | 비고 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 2026-06-08 11:05:13 | `20260608-110513_b9bea3` | 3 | 100% | 616.7ms | 3/3 | synthetic mp4 3개, `worker-1`, frame `1`, max token `128` |

위 평가는 로컬에서 생성한 짧은 mp4로 vLLM/GPU 파이프라인을 반복 검증한 결과입니다.
공개 YouTube 또는 실제 관제 영상 품질 평가는 별도 샘플 목록을 넣어 다시 수행해야 합니다.
해당 synthetic 샘플에서는 모델이 화면의 영문 텍스트를 그대로 반환해 한국어 fallback이 발생했습니다.

`k8s/` 폴더는 다음 파일로 구성됩니다.

| 파일 | 내용 |
| --- | --- |
| `k8s/README.md` | Linux/Kubernetes GPU 노드 적용 순서와 검증 기준 |
| `k8s/nvidia-device-plugin-timeslicing-config.yaml` | NVIDIA device-plugin time-slicing ConfigMap |
| `k8s/vllm-qwen3-vl-2b-deployment.yaml` | vLLM Qwen3-VL PoC Deployment/Service. time-slicing 검증 예시로 replica `2` |
| `k8s/kustomization.yaml` | 위 manifest를 한 번에 dry-run/apply하기 위한 kustomize 목록 |

기본 문법 확인은 다음 명령으로 수행합니다.

```bash
kubectl apply --dry-run=client -f k8s/nvidia-device-plugin-timeslicing-config.yaml
kubectl apply --dry-run=client -f k8s/vllm-qwen3-vl-2b-deployment.yaml
kubectl apply --dry-run=client -k k8s
```

실제 GPU Kubernetes 서버에서는 다음 순서로 검증합니다.

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update
kubectl apply -f k8s/nvidia-device-plugin-timeslicing-config.yaml
helm upgrade -i nvdp nvdp/nvidia-device-plugin \
  --namespace nvidia-device-plugin \
  --create-namespace \
  --set config.name=nvidia-device-plugin-config \
  --set config.default=time-slicing-config.yaml
kubectl get pods -n nvidia-device-plugin -o wide
kubectl logs -n nvidia-device-plugin -l app.kubernetes.io/name=nvidia-device-plugin --tail=200
kubectl describe nodes
```

화면의 `Time-slicing 로그 수집` 버튼을 누르면 검증 1회마다 `logs/timeslicing/{timestamp}_{run_id}/` 폴더가 생성됩니다.

| 경로 | 내용 |
| --- | --- |
| `summary.json` | 전체 상태, 단계별 check, 원인 코드, 원본 로그 경로를 담은 JSON 리포트 |
| `summary.md` | 사람이 읽기 쉬운 Markdown 검증 리포트 |
| `raw/*.txt` | `kubectl`, `nvidia-smi` 등 실제 명령의 stdout/stderr 원본 |
| `manifest/` | 검증 시점의 time-slicing/vLLM manifest 복사본 |

| 상태 | 의미 |
| --- | --- |
| `success` | 핵심 Kubernetes/GPU check가 모두 성공 |
| `partial` | 일부 check는 성공했지만 device-plugin, GPU 리소스 등 일부 검증이 실패 |
| `not_available` | 로컬 Windows처럼 Kubernetes 클러스터 연결이 없어 검증할 수 없는 상태 |
| `failed` | 핵심 check가 모두 실패했거나 manifest 등 필수 조건이 부족한 상태 |

중요: NVIDIA device plugin time-slicing의 `replicas`는 GPU 접근 슬롯을 oversubscribe하는 설정입니다. 실제 VRAM을 물리적으로 나누는 기능이 아니므로 여러 Pod가 같은 GPU에 큰 모델을 동시에 올리면 OOM이 발생할 수 있습니다.

## 참고 출처

- Qwen3-VL-2B-Instruct 모델 카드: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
- vLLM multimodal inputs 문서: https://docs.vllm.ai/en/v0.9.2/features/multimodal_inputs.html
- vLLM Docker 배포 문서: https://docs.vllm.ai/en/stable/deployment/docker.html
- NVIDIA k8s-device-plugin: https://github.com/NVIDIA/k8s-device-plugin
