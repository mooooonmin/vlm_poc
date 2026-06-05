# 영상 입력 VLM 분석 PoC

이 프로젝트는 영상 파일 또는 영상 URL을 입력하면 프레임을 균등 샘플링하고,
샘플 프레임을 `Qwen/Qwen3-VL-2B-Instruct`에 전달해 한국어 분석 결과를 받는 임시 PoC입니다.

## 목표

- RTX 4070 Ti 환경에서 CUDA/GPU 사용 가능 여부를 확인합니다.
- vLLM Docker 컨테이너로 Qwen VLM을 OpenAI 호환 API 형태로 서빙합니다.
- Python FastAPI 화면에서 영상 업로드, 영상 URL 입력, 프레임 샘플링, VLM 분석 요청을 테스트합니다.
- Kubernetes time-slicing은 로컬 직접 실행 대상이 아니라, 향후 Linux/K8s GPU 노드 검증 대상으로 둡니다.

## 모델 기준

기본 모델:

- `Qwen/Qwen3-VL-2B-Instruct`
- 라이선스 근거: Hugging Face 모델 카드에 `License: apache-2.0`로 표시되어 있습니다.
- VLM 근거: Hugging Face 태스크가 `Image-Text-to-Text`로 표시되어 있습니다.
- vLLM 근거: Hugging Face 모델 카드에 `vllm serve "Qwen/Qwen3-VL-2B-Instruct"` 예시가 포함되어 있습니다.
- 출처: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct

대체 후보:

- `Qwen/Qwen3-VL-4B-Instruct-FP8`
- `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`

주의: 한국어 성능은 실제 한국어 CCTV/관제 매뉴얼 프롬프트로 별도 검증해야 합니다.

## 실행 순서

1. Python 가상환경을 준비합니다.

Windows PowerShell:

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
```

Linux:

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
```

2. FastAPI 테스트 화면을 실행합니다.

```bash
python app.py
```

3. 브라우저에서 접속합니다.

```text
http://127.0.0.1:8080
```

4. 화면 상단에서 GPU 상태를 확인하고, `vLLM 시작` 버튼으로 Docker 컨테이너를 실행합니다.

5. 영상 파일을 업로드하거나 영상 URL을 입력한 뒤 `영상 분석 실행`을 누릅니다.

## Python 앱에서 제공하는 기능

- `/api/gpu-status`: `nvidia-smi` 기반 GPU/CUDA 상태 확인.
- `/api/vllm-status`: Docker 컨테이너와 `http://localhost:8000/v1/models` 기준 vLLM 상태 확인.
- `/api/start-vllm`: Python에서 `docker run`을 호출해 vLLM 컨테이너 시작.
- `/api/stop-vllm`: Python에서 `docker rm -f`를 호출해 vLLM 컨테이너 중지.
- `/api/analyze-video`: 영상 저장, 균등 프레임 추출, base64 data URL 변환, vLLM 분석 요청.
  - 일반 mp4/mov 파일 URL은 `requests`로 직접 다운로드합니다.
  - YouTube URL(`youtube.com`, `youtu.be`)은 실제 mp4 파일 URL이 아니므로 `yt-dlp`로 다운로드한 뒤 OpenCV로 엽니다.
  - YouTube는 영상별 제한, 로그인 필요, 지역 제한, 네트워크 정책에 따라 실패할 수 있습니다.
- `/api/timeslicing`: K8s time-slicing 초안과 주의사항 확인.

## 기본 설정

- 모델: `Qwen/Qwen3-VL-2B-Instruct`
- vLLM 포트: `8000`
- FastAPI 화면 포트: `8080`
- 샘플 프레임 수: `6`
- `GPU_MEMORY_UTILIZATION`: `0.85`
- `MAX_MODEL_LEN`: `8192`
- 컨테이너 이름: `vlm-vllm-qwen`

RTX 4070 Ti에서 OOM이 발생하면 화면에서 샘플 프레임 수를 `4`로 낮추고,
환경 변수 `MAX_MODEL_LEN=4096`으로 낮춰 다시 테스트합니다.

## 참고 스크립트

기존 shell 스크립트는 참고용으로 남겨 둡니다. 현재 PoC 조작은 Python 화면에서 수행하는 것을 기준으로 합니다.

```bash
bash scripts/check_cuda.sh
bash scripts/serve_vllm.sh
bash scripts/test_vlm_request.sh
```

## 외부 근거

- Qwen3-VL-2B-Instruct 모델 카드: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
- vLLM multimodal inputs 문서: https://docs.vllm.ai/en/v0.9.2/features/multimodal_inputs.html
- vLLM Docker 배포 문서: https://docs.vllm.ai/en/stable/deployment/docker.html
- NVIDIA k8s-device-plugin: https://github.com/NVIDIA/k8s-device-plugin
