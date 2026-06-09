# 설정 가이드

이 문서는 화면과 런타임에서 사용하는 주요 설정값을 정리합니다.

## 기본 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `APP_HOST` | `127.0.0.1` | FastAPI 바인딩 주소 |
| `APP_PORT` | `8080` | FastAPI 기본 포트 |
| `DEFAULT_SAMPLING_MODE` | `segment` | 기본 프레임 샘플링 방식 |
| `DEFAULT_MAX_TOKENS` | `1024` | vLLM 응답 최대 토큰 |
| `MAX_SAMPLE_FRAMES` | `120` | 화면과 job에 저장할 최대 추출 프레임 수 |
| `MAX_VLLM_INPUT_FRAMES` | `30` | vLLM에 기본 전송할 최대 프레임 수 |
| `MAX_VLLM_INPUT_FRAMES_MID_TOKEN` | `32` | `max_tokens <= 768`일 때 전송 상한 |
| `MAX_VLLM_INPUT_FRAMES_LOW_TOKEN` | `36` | `max_tokens <= 512`일 때 전송 상한 |
| `MAX_UPLOAD_BYTES` | `1073741824` | 업로드 파일 최대 크기 |
| `MAX_VIDEO_DURATION_SEC` | `1800` | 분석 대상 영상 길이 제한 |
| `VLLM_WORKERS` | 미설정 | 여러 vLLM endpoint를 쉼표로 등록 |

## 화면 입력 범위

| 항목 | 범위 | 기본값 |
| --- | ---: | ---: |
| 최대 프레임 수 | `1~120` | `30` |
| 최대 토큰 | `64~2048` | `1024` |
| 샘플링 방식 | `segment`, `one_fps` | `segment` |

## 추출 프레임과 vLLM 전송 프레임

화면에는 최대 `MAX_SAMPLE_FRAMES`만큼 프레임을 추출해 보여줄 수 있습니다. 하지만 모든 프레임을 vLLM에 보내면 모델 context length를 초과할 수 있습니다. 그래서 vLLM 전송 프레임 수는 별도 상한을 사용합니다.

| 최대 토큰 | vLLM 전송 프레임 상한 |
| ---: | ---: |
| `512` 이하 | `36` |
| `768` 이하 | `32` |
| `1024` 이하 | `30` |
| `1024` 초과 | `24` |

context length 초과 또는 vLLM 내부 500 오류가 발생하면 앱은 중복 프레임 제거와 프레임 축소 재시도를 수행합니다.

## 모델과 endpoint

| 항목 | 기본값 |
| --- | --- |
| 모델 | `Qwen/Qwen3-VL-2B-Instruct` |
| endpoint | `http://localhost:8000/v1/chat/completions` |
| vLLM models endpoint | `http://localhost:8000/v1/models` |

화면에서는 사용 모델을 표시만 하고 수정하지 않습니다. 실제 모델을 바꾸려면 vLLM 컨테이너 시작 설정과 앱 설정을 함께 바꿔야 합니다.

## 다중 worker

`VLLM_WORKERS`를 설정하면 여러 vLLM endpoint를 worker로 등록할 수 있습니다.

```powershell
$env:VLLM_WORKERS="worker-1=http://host1:8000/v1/chat/completions,worker-2=http://host2:8000/v1/chat/completions"
python app.py
```

로컬 RTX 4070 Ti 기본 검증은 worker 1개입니다. worker를 늘리는 것은 별도 GPU/Kubernetes 환경에서 검증해야 합니다.
