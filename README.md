# 영상 입력 VLM 분석 PoC

영상 파일 또는 YouTube URL을 입력하면 프레임을 균등 샘플링하고, 샘플 프레임을 `Qwen/Qwen3-VL-2B-Instruct`에 전달해 한국어 분석 결과를 받는 임시 PoC입니다.

## 목표

- RTX 4070 Ti 환경에서 CUDA/GPU 사용 가능 여부를 확인합니다.
- vLLM Docker 컨테이너로 Qwen VLM을 OpenAI 호환 API 형태로 서빙합니다.
- FastAPI 화면에서 영상 입력, 분석 job 생성, 프레임 미리보기, VLM 응답 확인을 테스트합니다.
- Kubernetes time-slicing은 로컬 Windows/Docker에서 직접 적용하지 않고, 추후 Linux/K8s GPU 노드 검증 대상으로 문서와 manifest만 준비합니다.

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
python app.py
```

기본 접속 주소:

```text
http://127.0.0.1:8080
```

8080 포트가 사용 중이면 앱이 8081, 8082처럼 다음 빈 포트를 자동으로 찾아 실행합니다. 실제 접속 주소는 `python app.py` 콘솔 출력에서 확인합니다.

## Python API

- `GET /api/gpu-status`: `nvidia-smi` 기반 GPU/CUDA 상태 확인.
- `GET /api/vllm-status`: Docker 컨테이너와 `http://localhost:8000/v1/models` 기준 vLLM 상태 확인.
- `GET /api/vllm/logs`: vLLM 컨테이너 로그 tail 조회.
- `POST /api/start-vllm`: Python에서 Docker 기반 vLLM 컨테이너 시작.
- `POST /api/stop-vllm`: vLLM 컨테이너 종료 및 GPU 점유 해제.
- `POST /api/jobs/video`: 영상 분석 job 생성. 즉시 `job_id`를 반환합니다.
- `GET /api/jobs/{job_id}`: 분석 상태, 추출 프레임, 결과, 에러 조회.
- `GET /api/jobs`: 최근 분석 job 목록 조회.
- `POST /api/analyze-video`: 기존 호환용 API. 내부적으로 job을 생성하고 `job_id`를 반환합니다.
- `GET /api/timeslicing`: K8s time-slicing 초안과 주의사항 확인.
- `POST /api/timeslicing/logs`: K8s/time-slicing 검증용 명령 결과를 `logs/timeslicing/...`에 저장.

## 분석 처리 방식

- 기본 처리 방식은 순차 처리입니다.
- 단일 RTX 4070 Ti에서 여러 VLM 요청을 동시에 보내면 VRAM 부족과 지연 원인을 구분하기 어려워 기본 병렬 처리는 비활성화합니다.
- 각 분석 요청은 `job_id`를 받고, `tmp/jobs/{job_id}/job.json`에 상태와 결과를 저장합니다.
- 상태값은 `queued`, `running`, `done`, `failed`입니다.

## 기본 설정

- 모델: `Qwen/Qwen3-VL-2B-Instruct`
- vLLM 포트: `8000`
- FastAPI 화면 포트: `8080`
- 샘플 프레임 수: 기본 `6`, 최대 `12`
- `GPU_MEMORY_UTILIZATION`: `0.85`
- `MAX_MODEL_LEN`: `8192`
- 업로드 파일 제한: 기본 `1GB`
- 영상 길이 제한: 기본 `1800초`
- 컨테이너 이름: `vlm-vllm-qwen`

RTX 4070 Ti에서 OOM이 발생하면 샘플 프레임 수를 `4`로 낮추고, 환경 변수 `MAX_MODEL_LEN=4096`으로 낮춰 다시 테스트합니다.

## YouTube URL 주의사항

YouTube URL은 직접 mp4 파일 주소가 아니므로 `yt-dlp`로 실제 영상 파일을 내려받은 뒤 OpenCV로 엽니다. 공개 영상이어도 비공개 전환, 연령 제한, 지역 제한, 로그인 필요, 네트워크 제한, YouTube 정책 변화에 따라 실패할 수 있습니다.

## Time-slicing 검증

로컬 Windows/Docker 테스트에서는 Kubernetes time-slicing을 실제 적용하지 않습니다. 현재 PoC는 다음 근거를 준비하는 수준입니다.

```bash
kubectl apply --dry-run=client -f k8s/nvidia-device-plugin-timeslicing-config.yaml
kubectl apply --dry-run=client -f k8s/vllm-qwen3-vl-2b-deployment.yaml
kubectl -n kube-system logs -l app=nvidia-device-plugin-daemonset --tail=200
kubectl describe nodes
```

중요: NVIDIA device plugin time-slicing의 `replicas`는 GPU 접근 슬롯을 oversubscribe하는 설정입니다. 실제 VRAM을 물리적으로 나누는 기능이 아니므로 여러 Pod가 같은 GPU에 큰 모델을 동시에 올리면 OOM이 발생할 수 있습니다.

## 참고 출처

- Qwen3-VL-2B-Instruct 모델 카드: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
- vLLM multimodal inputs 문서: https://docs.vllm.ai/en/v0.9.2/features/multimodal_inputs.html
- vLLM Docker 배포 문서: https://docs.vllm.ai/en/stable/deployment/docker.html
- NVIDIA k8s-device-plugin: https://github.com/NVIDIA/k8s-device-plugin
