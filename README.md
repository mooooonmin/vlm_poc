# 영상 입력 VLM 분석 PoC

영상 파일 또는 YouTube URL을 등록하고, 같은 영상에 대해 질문을 이어서 하는 채팅형 VLM 분석 PoC입니다. 화면은 `대화 목록 | 채팅창 | 영상/프레임 패널` 구조이며, 실제 분석은 Docker로 실행한 vLLM OpenAI 호환 API를 호출합니다.

## 빠른 시작

Windows PowerShell 기준입니다.

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
docker pull vllm/vllm-openai:latest
python app.py
```

기본 접속 주소는 `http://127.0.0.1:8080`입니다. 포트가 사용 중이면 앱이 다음 빈 포트를 찾아 실행하며, 실제 주소는 콘솔 출력에서 확인합니다.

## 사용 흐름

1. `새 대화`를 누릅니다.
2. 오른쪽 영상 패널에서 파일 또는 YouTube URL을 등록합니다.
3. 채팅창에 질문을 입력합니다.
4. 답변, 영상 미리보기, 추출 프레임, job 로그 경로를 확인합니다.
5. 테스트가 끝나면 `vLLM 종료`로 GPU 점유를 해제합니다.

## 현재 검증 범위

| 항목 | 상태 |
| --- | --- |
| 로컬 PoC | Windows + Docker + RTX 4070 Ti + vLLM 단일 worker 기준 |
| UI | 채팅형 대화 세션, 영상 등록, 질문/답변, 영상 미리보기, 프레임 확대 |
| 영상 입력 | 파일 업로드, 직접 영상 URL, 공개 YouTube URL |
| 분석 방식 | 구간 대표 프레임 또는 1fps 프레임 추출 후 멀티 이미지 입력 |
| vLLM | `Qwen/Qwen3-VL-2B-Instruct` 기본 모델 |
| 다중 worker | `VLLM_WORKERS` 기반 구조 준비, 로컬 기본값은 worker 1개 |
| Kubernetes time-slicing | manifest와 검증 로그 구조 준비, 실환경 검증은 미완료 |

## 주요 문서

| 문서 | 내용 |
| --- | --- |
| `docs/QUICK_START_CHECKLIST.md` | 실행 전 확인 체크리스트 |
| `docs/DEVELOPMENT_GUIDE.md` | 설치, 실행, vLLM 시작, 외부 접속 안내 |
| `docs/DEPLOYMENT_GUIDE.md` | Docker/vLLM 및 Kubernetes 검증 절차 |
| `docs/CONFIGURATION.md` | 환경변수와 기본 설정 |
| `docs/ARCHITECTURE.md` | 채팅 세션, job, worker 처리 구조 |
| `docs/TROUBLESHOOTING.md` | 자주 발생하는 오류 확인 방법 |
| `docs/TEST_RESULTS.md` | 검증 결과 기록 |
| `k8s/README.md` | Kubernetes time-slicing 검증 안내 |

## 기본 모델

기본 모델은 `Qwen/Qwen3-VL-2B-Instruct`입니다. 모델 카드는 `apache-2.0` 라이선스와 vLLM 실행 예시를 제공합니다.

출처: [Qwen/Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)

## 참고 문서

- [vLLM Docker 문서](https://docs.vllm.ai/en/stable/deployment/docker.html)
- [NVIDIA k8s-device-plugin](https://github.com/NVIDIA/k8s-device-plugin)
