# 테스트 결과 기록

이 문서는 실행 결과와 검증 근거만 관리합니다. 사용법과 구조 설명은 `README.md`를 기준으로 봅니다.

## 로컬 환경 확인

| 항목 | 확인 결과 | 근거 |
| --- | --- | --- |
| GPU | NVIDIA GeForce RTX 4070 Ti | `nvidia-smi` |
| Docker | Docker Desktop Linux Engine 응답 | `docker version` |
| vLLM 컨테이너 | `vlm-vllm-qwen`, `vllm/vllm-openai:latest`, port `8000` | `docker ps` |
| vLLM 모델 API | `Qwen/Qwen3-VL-2B-Instruct`, `max_model_len=8192` | `GET http://localhost:8000/v1/models` |
| Kubernetes | 현재 로컬 context 미설정 또는 API 조회 실패 | `kubectl config current-context`, `kubectl get nodes` |

Kubernetes time-slicing 실검증은 아직 완료되지 않았습니다. 현재 로컬 환경에서 확인된 범위는 Docker GPU 기반 vLLM 단일 worker와 평가 러너입니다.

## Evaluation Runs

| 일시 | run_id | 샘플 | 성공률 | 평균 처리시간 | 한국어 fallback | 비고 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 2026-06-08 11:03:08 | `20260608-110308_332d8f` | 1 | 100% | 확인됨 | 확인됨 | evaluation runner 최초 synthetic 1개 검증 |
| 2026-06-08 11:05:13 | `20260608-110513_b9bea3` | 3 | 100% | 616.7ms | 3/3 | synthetic mp4 3개, `worker-1`, frame `1`, max token `128` |
| 2026-06-08 11:07:39 | `20260608-110739_3907e4` | 1 | 100% | 확인됨 | 1/1 | `korean_fallback_rate=1.0` 필드 검증 |

## 주요 관찰

- synthetic mp4 샘플은 모두 `done`으로 완료되어 로컬 vLLM/GPU 파이프라인 자체는 동작했습니다.
- synthetic 샘플은 화면에 영어 텍스트를 넣은 짧은 영상이라, 모델이 영어 텍스트를 그대로 반환하는 경향이 확인됐습니다.
- 위 이유로 한국어 retry/repair 후에도 fallback이 발생했습니다. 실제 관제 영상 또는 한국어 텍스트가 없는 영상에서는 별도 검증이 필요합니다.
- 현재 기록은 성능 벤치마크가 아니라 PoC 기능 검증 기록입니다.

## UI Layout QA

| 일시 | 화면 크기 | 확인 결과 | 근거 |
| --- | --- | --- | --- |
| 2026-06-08 | 1365x768 | 런타임 상세를 접은 상태에서 영상 입력, 분석 결과, 최근 작업을 3열 대시보드로 표시했습니다. 전체 페이지 스크롤 대신 입력/결과/최근 작업 패널 내부 스크롤을 사용합니다. | `tmp/layout_qa_loaded_compact_worker.png`, Edge headless screenshot |

검증 범위는 테스트 화면의 배치 확인입니다. 실제 영상 분석 품질이나 vLLM 처리 성능 검증은 이 항목에 포함하지 않았습니다.

## 검증 명령

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py runtime_utils.py job_store.py worker_registry.py evaluation_runner.py
node --check static\app.js
git diff --check
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_qa_loaded_compact_worker.png http://127.0.0.1:8080
Invoke-RestMethod -Uri http://localhost:8000/v1/models -TimeoutSec 10
.\.venv\Scripts\python.exe evaluation_runner.py --synthetic-count 3 --frame-count 1 --max-tokens 128 --timeout-sec 180
```

## 다음 실제 테스트 필요 항목

- 실제 mp4 3~5개
- 공개 YouTube URL 3~5개
- 긴 영상에서 frame count `4`, `6`, `8` 비교
- 한국어 fallback 비율이 synthetic 샘플과 실제 영상에서 어떻게 달라지는지 비교
