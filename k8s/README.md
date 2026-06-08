# Kubernetes Time-slicing 적용 패키지

이 폴더는 Linux/Kubernetes GPU 노드에서 vLLM PoC를 검증하기 위한 초안입니다. 로컬 Windows/Docker 환경에서는 실제 time-slicing을 적용하지 않습니다.

## 전제 조건

| 항목 | 확인 내용 |
| --- | --- |
| GPU 노드 | NVIDIA GPU가 장착된 Linux Kubernetes node |
| NVIDIA 드라이버 | node에서 `nvidia-smi`가 정상 동작 |
| Container runtime | Kubernetes runtime에서 NVIDIA GPU 전달 가능 |
| kubectl | 대상 클러스터 context 연결 |
| Helm | NVIDIA device plugin 설치/업그레이드에 사용 |

## 적용 순서

1. NVIDIA device plugin Helm repo를 추가합니다.

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update
```

2. time-slicing ConfigMap을 적용합니다.

```bash
kubectl apply -f k8s/nvidia-device-plugin-timeslicing-config.yaml
```

3. device-plugin을 ConfigMap 기준으로 설치 또는 업그레이드합니다.

```bash
helm upgrade -i nvdp nvdp/nvidia-device-plugin \
  --namespace nvidia-device-plugin \
  --create-namespace \
  --set config.name=nvidia-device-plugin-config \
  --set config.default=time-slicing-config.yaml
```

4. device-plugin Pod 상태와 로그를 확인합니다.

```bash
kubectl get pods -n nvidia-device-plugin -o wide
kubectl logs -n nvidia-device-plugin -l app.kubernetes.io/name=nvidia-device-plugin --tail=200
```

5. node에 GPU 리소스가 노출되는지 확인합니다.

```bash
kubectl describe nodes | grep -n "nvidia.com/gpu"
```

6. vLLM PoC Deployment를 적용합니다.

현재 manifest는 time-slicing 검증을 위해 vLLM Pod `replicas: 2`로 작성되어 있습니다.
이 값은 "두 개의 vLLM 서버가 같은 GPU의 oversubscribe slot에 스케줄링되는지" 확인하기 위한 PoC 값입니다.
VRAM이 둘로 나뉘는 것은 아니므로 모델 2개가 동시에 올라가지 못하면 OOM이 발생할 수 있습니다.

```bash
kubectl apply -f k8s/vllm-qwen3-vl-2b-deployment.yaml
kubectl get pods -n vlm-poc -o wide
kubectl logs -n vlm-poc -l app=vllm-qwen3-vl-2b --tail=200
```

7. FastAPI 앱을 클러스터 밖에서 실행한다면 vLLM endpoint를 port-forward로 노출한 뒤 `VLLM_WORKERS`에 등록합니다.

```bash
kubectl port-forward -n vlm-poc service/vllm-qwen3-vl-2b 8000:8000
```

단일 Service를 사용하면 Kubernetes Service가 HTTP 요청을 Pod들에 분산합니다.
Pod별 배정 로그까지 명확히 보고 싶다면 Pod별 Service 또는 별도 port-forward를 구성하고 `VLLM_WORKERS`에 endpoint를 여러 개 넣어 테스트합니다.

## 검증 기준

| 단계 | 성공 기준 |
| --- | --- |
| ConfigMap | `nvidia-device-plugin-config`가 `nvidia-device-plugin` namespace에 존재 |
| device-plugin | Pod가 Running 상태이고 로그에서 설정 로드 오류가 없음 |
| GPU 리소스 | `kubectl describe nodes`에 `nvidia.com/gpu`가 노출 |
| vLLM Pod | `vlm-poc` namespace의 vLLM Pod 2개가 Running 상태 |
| vLLM API | Pod 내부 또는 Service 경유로 `/v1/models`가 응답 |

실제 검증 결과는 루트의 `docs/TEST_RESULTS.md`에 기록합니다.
이 문서는 적용 절차와 성공 기준만 관리합니다.

## 주의사항

time-slicing은 GPU 접근 슬롯을 oversubscribe하는 기능입니다. VRAM을 물리적으로 나누지 않기 때문에 vLLM Pod를 여러 개 동시에 띄우면 OOM이 발생할 수 있습니다. OOM이 나면 `--max-model-len`을 `4096`으로 낮추거나 `--gpu-memory-utilization`을 `0.80` 이하로 낮춰 테스트합니다.
