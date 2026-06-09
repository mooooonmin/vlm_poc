# 배포/실행 가이드

현재 프로젝트는 운영 배포가 아니라 PoC 실행과 검증을 목표로 합니다. 로컬 Windows/Docker 실행과 Linux/Kubernetes time-slicing 검증 준비를 분리해 관리합니다.

## 로컬 Docker 실행

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
docker pull vllm/vllm-openai:latest
python app.py
```

화면에서 `vLLM 시작`을 누르면 앱이 Docker 컨테이너 `vlm-vllm-qwen`을 시작합니다. 컨테이너가 준비되면 `/v1/models`가 응답하고 worker가 `ready`로 표시됩니다.

## 외부 사용자 접속

같은 네트워크의 다른 사용자가 이 PC에서 테스트해야 하면 다음처럼 실행합니다.

```powershell
$env:APP_HOST="0.0.0.0"
$env:APP_PORT="8080"
python app.py
```

다른 사용자는 `http://<PC IPv4 주소>:8080`으로 접속합니다. Windows 방화벽에서 TCP 8080 인바운드 허용이 필요할 수 있습니다.

## vLLM 사전 pull

처음 실행 시간을 줄이려면 vLLM 이미지를 미리 받습니다.

```powershell
docker pull vllm/vllm-openai:latest
```

모델 파일은 vLLM 컨테이너가 처음 시작될 때 Hugging Face cache에 다운로드됩니다.

## Kubernetes time-slicing 검증

로컬 Windows/Docker에서는 Kubernetes time-slicing을 실제 적용하지 않습니다. 실검증에는 Linux/Kubernetes GPU node와 NVIDIA device-plugin 또는 GPU Operator 설정이 필요합니다.

검증 준비 파일:

| 파일 | 내용 |
| --- | --- |
| `k8s/nvidia-device-plugin-timeslicing-config.yaml` | time-slicing ConfigMap 예시 |
| `k8s/vllm-qwen3-vl-2b-deployment.yaml` | vLLM Deployment/Service 예시 |
| `k8s/kustomization.yaml` | namespace와 manifest 묶음 |

문법 확인:

```powershell
kubectl apply --dry-run=client -f k8s/
```

실환경 확인:

```powershell
kubectl get pods -n kube-system
kubectl describe node
kubectl get pods -n vlm-poc
```

앱 화면의 `Time-slicing 로그` 버튼은 현재 환경에서 kubectl, 클러스터 연결, NVIDIA device-plugin, GPU resource 노출 여부를 리포트로 저장합니다.

## 현재 배포 한계

| 항목 | 상태 |
| --- | --- |
| 운영 DB | 미사용 |
| 인증/권한 | 미구현 |
| HTTPS | 미구현 |
| Kubernetes time-slicing 실적용 | 미완료 |
| vLLM 2개 이상 Pod 실부하 | 미완료 |
