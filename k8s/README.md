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

```bash
kubectl apply -f k8s/vllm-qwen3-vl-2b-deployment.yaml
kubectl get pods -n vlm-poc -o wide
kubectl logs -n vlm-poc -l app=vllm-qwen3-vl-2b --tail=200
```

## 검증 기준

| 단계 | 성공 기준 |
| --- | --- |
| ConfigMap | `nvidia-device-plugin-config`가 `nvidia-device-plugin` namespace에 존재 |
| device-plugin | Pod가 Running 상태이고 로그에서 설정 로드 오류가 없음 |
| GPU 리소스 | `kubectl describe nodes`에 `nvidia.com/gpu`가 노출 |
| vLLM Pod | `vlm-poc` namespace의 Pod가 Running 상태 |
| vLLM API | Pod 내부 또는 Service 경유로 `/v1/models`가 응답 |

## 주의사항

time-slicing은 GPU 접근 슬롯을 oversubscribe하는 기능입니다. VRAM을 물리적으로 나누지 않기 때문에 vLLM Pod를 여러 개 동시에 띄우면 OOM이 발생할 수 있습니다. OOM이 나면 `--max-model-len`을 `4096`으로 낮추거나 `--gpu-memory-utilization`을 `0.80` 이하로 낮춰 테스트합니다.
