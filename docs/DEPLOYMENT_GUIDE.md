# 배포/서빙 가이드

이 문서는 로컬 Docker 기준 vLLM 서빙과 Linux/Kubernetes 기준 time-slicing 검증 절차를 정리합니다. 현재 프로젝트의 실제 운영 배포가 아니라 PoC 서버 실행과 검증을 목표로 합니다.

## 1. 로컬 Docker 서빙 기준

| 항목 | 기준 |
| --- | --- |
| 앱 서버 | Windows에서 `python app.py` 실행 |
| vLLM 서버 | Docker 컨테이너 `vllm/vllm-openai:latest` |
| GPU | NVIDIA GPU, `nvidia-smi` 정상 동작 |
| 기본 모델 | `Qwen/Qwen3-VL-2B-Instruct` |
| 기본 포트 | FastAPI `8080`, vLLM `8000` |

## 2. 사전 확인

```powershell
nvidia-smi
docker version
docker ps
```

`nvidia-smi`가 실패하면 GPU 드라이버 또는 NVIDIA 런타임 환경을 먼저 확인합니다. `docker version`이 실패하면 Docker Desktop 실행 상태를 확인합니다.

## 3. vLLM 이미지 받기

```powershell
docker pull vllm/vllm-openai:latest
```

이미지를 미리 받아두면 웹 화면에서 `vLLM 시작 / GPU 점유`를 눌렀을 때 대기 시간이 줄어듭니다.

## 4. vLLM 컨테이너 수동 실행

웹 화면 버튼 대신 명령어로 직접 vLLM 서버를 띄우려면 아래 명령을 사용합니다.

```powershell
$env:HF_HOME="$HOME\.cache\huggingface"

docker rm -f vlm-vllm-qwen

docker run -d `
  --name vlm-vllm-qwen `
  --gpus all `
  --ipc=host `
  -p 8000:8000 `
  -v "${env:HF_HOME}:/root/.cache/huggingface" `
  vllm/vllm-openai:latest `
  --model Qwen/Qwen3-VL-2B-Instruct `
  --host 0.0.0.0 `
  --port 8000 `
  --gpu-memory-utilization 0.85 `
  --max-model-len 8192 `
  --trust-remote-code
```

Hugging Face 토큰이 필요하면 `docker run`에 아래 옵션을 추가합니다.

```powershell
-e HF_TOKEN="$env:HF_TOKEN"
```

현재 기본 모델은 공개 모델이므로 일반적으로 `HF_TOKEN` 없이 시작할 수 있습니다.

## 5. vLLM ready 확인

컨테이너 시작 직후에는 모델 다운로드와 로딩 때문에 바로 응답하지 않을 수 있습니다.

```powershell
docker logs --tail 120 vlm-vllm-qwen
Invoke-RestMethod -Uri http://localhost:8000/v1/models
```

`/v1/models`가 응답하면 FastAPI 앱에서 분석 요청을 보낼 수 있는 상태입니다.

## 6. FastAPI 앱 실행

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

접속:

```text
http://127.0.0.1:8080
```

같은 네트워크의 다른 사용자가 접속해야 하면 FastAPI를 `0.0.0.0`으로 바인딩합니다.

```powershell
$env:APP_HOST="0.0.0.0"
$env:APP_PORT="8080"
python app.py
```

이 PC의 IPv4 주소를 확인합니다.

```powershell
ipconfig
```

다른 사용자의 접속 주소:

```text
http://<이_PC의_IP주소>:8080
```

예:

```text
http://192.168.0.25:8080
```

Windows 방화벽 또는 사내 보안 정책이 있으면 TCP `8080` 인바운드 허용이 필요할 수 있습니다. vLLM `8000` 포트는 FastAPI 앱이 내부적으로 호출하므로 일반 테스트 사용자에게 직접 공개하지 않는 것을 기본으로 합니다.

앱 상태 확인:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/config
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/vllm-status
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/workers/refresh -Method Post
```

## 7. 로컬 분석 테스트

1. 웹 화면에서 영상 파일 또는 YouTube URL을 입력합니다.
2. 샘플링 방식은 기본 `구간 프레임`, 최대 프레임 수는 기본 `30`으로 시작합니다.
3. `영상 분석 batch 생성`을 누릅니다.
4. 결과와 `tmp/jobs/{job_id}/job.json`을 확인합니다.

반복 테스트 후 정리:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
```

`dry_run=true`는 실제 삭제 없이 정리 대상만 확인합니다.

## 8. Kubernetes time-slicing 검증 전제

Kubernetes 검증은 로컬 Windows/Docker 검증과 다릅니다. 아래 환경이 필요합니다.

| 항목 | 필요 조건 |
| --- | --- |
| GPU node | NVIDIA GPU가 장착된 Linux Kubernetes node |
| NVIDIA driver | node에서 `nvidia-smi` 정상 동작 |
| kubectl | 대상 cluster context 연결 |
| Helm | NVIDIA device-plugin 설치/업그레이드 |
| Container runtime | Pod에서 NVIDIA GPU 사용 가능 |

time-slicing은 GPU 메모리를 물리적으로 나누지 않습니다. Kubernetes scheduler에 GPU slot을 더 많이 노출하는 oversubscription 방식입니다.

## 9. Kubernetes time-slicing 적용

ConfigMap 적용:

```bash
kubectl apply -f k8s/nvidia-device-plugin-timeslicing-config.yaml
```

NVIDIA device-plugin 설치 또는 업그레이드:

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update

helm upgrade -i nvdp nvdp/nvidia-device-plugin \
  --namespace nvidia-device-plugin \
  --create-namespace \
  --set config.name=nvidia-device-plugin-config \
  --set config.default=time-slicing-config.yaml
```

확인:

```bash
kubectl get pods -n nvidia-device-plugin -o wide
kubectl logs -n nvidia-device-plugin -l app.kubernetes.io/name=nvidia-device-plugin --tail=200
kubectl describe nodes | grep -n "nvidia.com/gpu"
```

## 10. Kubernetes vLLM 배포

현재 manifest는 time-slicing 검증을 위해 vLLM Pod `replicas: 2`로 작성되어 있습니다.

```bash
kubectl apply -f k8s/vllm-qwen3-vl-2b-deployment.yaml
kubectl get pods -n vlm-poc -o wide
kubectl logs -n vlm-poc -l app=vllm-qwen3-vl-2b --tail=200
```

OOM이 발생하면 manifest의 값을 낮춰 재검증합니다.

| 설정 | 기본값 | OOM 시 권장 |
| --- | ---: | ---: |
| `--gpu-memory-utilization` | `0.85` | `0.80` 또는 `0.75` |
| `--max-model-len` | `8192` | `4096` |
| `replicas` | `2` | `1`로 낮춰 기준 확인 |

## 11. FastAPI와 Kubernetes vLLM 연결

클러스터 밖에서 FastAPI 앱을 실행한다면 port-forward로 vLLM Service를 노출합니다.

```bash
kubectl port-forward -n vlm-poc service/vllm-qwen3-vl-2b 8000:8000
```

로컬 FastAPI 실행:

```powershell
$env:VLLM_ENDPOINT="http://localhost:8000/v1/chat/completions"
$env:VLLM_MODELS_ENDPOINT="http://localhost:8000/v1/models"
python app.py
```

Pod별 endpoint를 따로 보고 싶다면 Pod별 Service 또는 port-forward를 구성한 뒤 `VLLM_WORKERS`에 쉼표로 등록합니다.

```powershell
$env:VLLM_WORKERS="http://localhost:8000/v1/chat/completions,http://localhost:8001/v1/chat/completions"
python app.py
```

## 12. 배포 검증 체크리스트

| 단계 | 확인 명령 |
| --- | --- |
| GPU 확인 | `nvidia-smi` |
| Docker 확인 | `docker version` |
| vLLM 이미지 확인 | `docker images vllm/vllm-openai` |
| vLLM ready | `Invoke-RestMethod http://localhost:8000/v1/models` |
| FastAPI ready | `Invoke-RestMethod http://127.0.0.1:8080/api/config` |
| worker ready | `Invoke-RestMethod http://127.0.0.1:8080/api/workers/refresh -Method Post` |
| K8s context | `kubectl config current-context` |
| device-plugin | `kubectl get pods -n nvidia-device-plugin -o wide` |
| vLLM Pod | `kubectl get pods -n vlm-poc -o wide` |

## 13. 현재 미포함 범위

- FastAPI 앱 자체를 Kubernetes에 배포하는 manifest
- Ingress, TLS, 인증, 사용자 관리
- 운영 DB 기반 job 저장소
- 장기 로그 보관 정책
- 실제 Linux/Kubernetes GPU node에서의 최종 성능 수치
