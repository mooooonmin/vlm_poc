# 설정 가이드

환경변수는 앱 시작 전에 PowerShell에서 설정하거나 `.env.example`을 참고해 별도 환경 로딩 방식으로 관리합니다. 현재 코드는 `.env` 파일을 자동 로드하지 않으므로, 필요한 값은 실행 쉘 환경에 직접 설정해야 합니다.

## 앱 서버

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `APP_HOST` | `127.0.0.1` | FastAPI 바인딩 host |
| `APP_PORT` | `8080` | FastAPI 시작 포트. 사용 중이면 다음 빈 포트 사용 |

예:

```powershell
$env:APP_PORT="8090"
python app.py
```

## vLLM 모델과 endpoint

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MODEL_ID` | `Qwen/Qwen3-VL-2B-Instruct` | vLLM이 로드할 모델 |
| `VLLM_ENDPOINT` | `http://localhost:8000/v1/chat/completions` | 분석 요청 endpoint |
| `VLLM_MODELS_ENDPOINT` | `http://localhost:8000/v1/models` | readiness 확인 endpoint |
| `HF_TOKEN` | 없음 | Hugging Face 인증 토큰 |
| `HF_HOME` | 사용자 홈의 `.cache/huggingface` | 모델 cache mount 위치 |

기본 모델을 바꾸면 이미 실행 중인 vLLM 컨테이너도 같은 모델로 다시 시작해야 합니다.

## Docker vLLM

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `CONTAINER_NAME` | `vlm-vllm-qwen` | 시작/종료할 Docker 컨테이너 이름 |
| `VLLM_IMAGE` | `vllm/vllm-openai:latest` | vLLM Docker 이미지 |
| `GPU_MEMORY_UTILIZATION` | `0.85` | vLLM GPU 메모리 사용 목표 비율 |
| `MAX_MODEL_LEN` | `8192` | vLLM 최대 컨텍스트 길이 |

RTX 4070 Ti에서 OOM이 발생하면 우선 아래처럼 낮춰 테스트합니다.

```powershell
$env:MAX_MODEL_LEN="4096"
$env:GPU_MEMORY_UTILIZATION="0.80"
python app.py
```

## 영상 입력 제한

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MAX_UPLOAD_BYTES` | `1073741824` | 업로드 파일 최대 크기, 기본 1GB |
| `MAX_VIDEO_DURATION_SEC` | `1800` | 분석 허용 영상 길이, 기본 30분 |

화면 입력 제한:
- 샘플 프레임 수: `1~12`
- 최대 토큰: `64~2048`
- batch 영상 수: 최대 `3`

## 한국어 응답 보정

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `KOREAN_RETRY_ENABLED` | `1` | 한국어 응답 실패 시 재요청/정리 사용 |
| `KOREAN_MIN_HANGUL` | `5` | 한국어 판정 최소 한글 글자 수 |
| `KOREAN_MIN_RATIO` | `0.2` | 전체 문자 대비 한글 비율 기준 |

## 다중 worker

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `VLLM_WORKERS` | 없음 | 쉼표로 구분한 vLLM endpoint 목록 |

예:

```powershell
$env:VLLM_WORKERS="http://localhost:8000/v1/chat/completions,http://localhost:8001/v1/chat/completions"
python app.py
```

로컬 RTX 4070 Ti 12GB에서 vLLM 컨테이너 2개 동시 실행은 OOM 가능성이 높습니다. 다중 worker는 Kubernetes time-slicing 검증 환경에서 우선 확인합니다.
