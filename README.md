# 영상 입력 VLM 분석 PoC

영상 파일 또는 YouTube URL을 입력하면 프레임을 균등 샘플링하고, 샘플 프레임을 `Qwen/Qwen3-VL-2B-Instruct`에 전달해 한국어 분석 결과를 받는 PoC입니다.

## 빠른 시작

Windows PowerShell 기준입니다.

```powershell
git clone https://github.com/mooooonmin/vlm_poc.git
cd vlm_poc
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
docker pull vllm/vllm-openai:latest
python app.py
```

접속 주소:

```text
http://127.0.0.1:8080
```

8080 포트가 사용 중이면 앱이 다음 빈 포트를 자동으로 찾아 실행합니다. 실제 주소는 `python app.py` 콘솔 출력에서 확인합니다.

## 현재 검증 범위

| 항목 | 상태 |
| --- | --- |
| 로컬 PoC | Windows + Docker + RTX 4070 Ti + vLLM 단일 worker |
| 영상 입력 | 파일 업로드, 직접 영상 URL, YouTube URL |
| 분석 방식 | 원본 영상 대신 균등 샘플 프레임을 멀티 이미지로 전달 |
| 다중 입력 | 최대 3개 영상 batch 생성 |
| worker 분산 | `VLLM_WORKERS` 기반 endpoint 분산 구조 준비 |
| time-slicing | 로컬 미적용. Linux/Kubernetes GPU node에서 별도 검증 필요 |

## 주요 문서

| 문서 | 내용 |
| --- | --- |
| `docs/DEVELOPMENT_GUIDE.md` | 설치, 실행, 기본 테스트 순서 |
| `docs/CONFIGURATION.md` | 환경변수와 기본 설정 |
| `docs/ARCHITECTURE.md` | 처리 흐름과 주요 모듈 구조 |
| `docs/TROUBLESHOOTING.md` | 자주 나는 오류와 확인 방법 |
| `docs/TEST_RESULTS.md` | 로컬 검증 결과 |
| `k8s/README.md` | Linux/Kubernetes time-slicing 검증 절차 |

## 기본 모델

기본 모델은 `Qwen/Qwen3-VL-2B-Instruct`입니다. 모델 카드 기준 라이선스는 `apache-2.0`이고, VLM 태스크와 vLLM 실행 예시가 제공됩니다.

출처: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct

## 공식 참고 문서

- vLLM Docker 문서: https://docs.vllm.ai/en/stable/deployment/docker.html
- NVIDIA k8s-device-plugin: https://github.com/NVIDIA/k8s-device-plugin
