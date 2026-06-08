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
| 2026-06-08 13:28:50 | `batch_1780892930_663d0c47` | 3 | 100% | job별 `723ms`, `567ms`, `567ms` | 0/3 | `/api/jobs/video-batch` synthetic mp4 3개, `worker-1`, frame `1`, max token `64` |
| 2026-06-08 15:32:27 | `batch_1780900347_0b8eb3f3` | 1 | 100% | 확인됨 | 0/1 | 구조화 프롬프트와 중복 줄 제거 후 synthetic mp4 1개 분석. `요약`, `주요 장면` 형식 응답 확인 |
| 2026-06-08 15:42:34 | `batch_1780900954_3e96e35c` | 1 | 100% | 확인됨 | 0/1 | 화면 입력은 사용자 분석 요청만 저장하고, 내부 출력 규격은 서버에서 합성되는 흐름 확인 |
| 2026-06-08 15:53:04 | `batch_1780901584_91187cf1` | 1 | 100% | 확인됨 | 0/1 | 원본 vLLM JSON을 화면에서 제거하고 `tmp/jobs/1780901584_fcfa4667/job.json`의 `raw` 필드에 저장되는 것 확인 |
| 2026-06-08 16:18:58 | `batch_1780902738_a16a95c5` | 1 | 100% | 확인됨 | 0/1 | 시간 질문용 샘플 프레임 시간표와 후처리 적용. `EVAL 1-2` 질문에 `답변: 약 1.00초` 확인 |
| 2026-06-08 16:26:05 | `batch_1780903565_31dc8038`, `batch_1780903567_b3fccd5d` | 2 | 100% | job별 `885ms`, `720ms` | 0/2 | 질문 유형별 내부 프롬프트 라우팅 적용. 시간 질문은 `답변: 약 1.50초`, 영상 종류 질문은 placeholder 없이 `답변: 확인 불가` 확인 |
| 2026-06-08 16:38:07 | `batch_1780903087_ce0b08f6` | 1 | 100% | 확인됨 | 0/1 | 영상 종류 질문 전용 지시 적용. synthetic 샘플의 `무슨 영상` 질문에서 사건 단정 없이 `확인 불가` 응답 확인 |

## 주요 관찰

- synthetic mp4 샘플은 모두 `done`으로 완료되어 로컬 vLLM/GPU 파이프라인 자체는 동작했습니다.
- synthetic 샘플은 화면에 영어 텍스트를 넣은 짧은 영상이라, 모델이 영어 텍스트를 그대로 반환하는 경향이 확인됐습니다.
- 위 이유로 한국어 retry/repair 후에도 fallback이 발생했습니다. 실제 관제 영상 또는 한국어 텍스트가 없는 영상에서는 별도 검증이 필요합니다.
- 현재 기록은 성능 벤치마크가 아니라 PoC 기능 검증 기록입니다.

## UI Layout QA

| 일시 | 화면 크기 | 확인 결과 | 근거 |
| --- | --- | --- | --- |
| 2026-06-08 | 1365x768 | 런타임 상세를 접은 상태에서 영상 입력, 분석 결과, 최근 작업을 3열 대시보드로 표시했습니다. 전체 페이지 스크롤 대신 입력/결과/최근 작업 패널 내부 스크롤을 사용합니다. | `tmp/layout_qa_loaded_compact_worker.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | 샘플 프레임 수 `1~12`, 최대 토큰 `64~2048` 범위 표시를 추가하고, 사용 모델을 비활성화 필드로 표시했습니다. | `tmp/layout_model_disabled.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | 영상 입력 슬롯 3개를 표시하고, 입력된 슬롯만 batch job으로 생성하는 화면을 확인했습니다. | `tmp/layout_batch_inputs.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | 흰색/옅은 회색 구획, 파란 액센트, 간결한 상태 카드 중심의 금융 앱형 UI 톤을 적용했습니다. | `tmp/layout_toss_like.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | 기본 화면의 긴 설명, 상세 런타임 도구, 평가 리포트, 상세 통계를 접힘 영역으로 이동해 입력/결과/최근 작업 중심으로 단순화했습니다. | `tmp/layout_simplified.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | VLM 응답 표시를 어두운 로그 박스에서 밝은 결과 카드로 변경했습니다. | `tmp/layout_answer_format.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | 프롬프트 입력을 내부 규격이 아닌 사용자 분석 요청 입력으로 단순화했습니다. | `tmp/layout_user_request_prompt.png`, Edge headless screenshot |
| 2026-06-08 | 1365x768 | 분석 결과 화면에서 `원본 JSON` 표시 영역을 제거하고, 원본 응답은 job 로그 파일에만 저장되도록 정리했습니다. | `tmp/layout_no_raw_json.png`, Edge headless screenshot |
| 2026-06-08 | API | 분석 form에 `action="/api/jobs/video-batch"`, `method="post"`, `enctype="multipart/form-data"`를 명시해 기본 form 제출이 `POST /`로 가며 405를 내는 경로를 차단했습니다. | `curl -i -X POST /api/jobs/video-batch`, `curl -i -X POST /` |

검증 범위는 테스트 화면의 배치 확인입니다. 실제 영상 분석 품질이나 vLLM 처리 성능 검증은 이 항목에 포함하지 않았습니다.

## 검증 명령

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py runtime_utils.py job_store.py worker_registry.py evaluation_runner.py
node --check static\app.js
git diff --check
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_qa_loaded_compact_worker.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_model_disabled.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_batch_inputs.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_toss_like.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_simplified.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_answer_format.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_user_request_prompt.png http://127.0.0.1:8080
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' --headless --disable-gpu --window-size=1365,768 --virtual-time-budget=5000 --screenshot=D:\project\vlm_test\tmp\layout_no_raw_json.png http://127.0.0.1:8080
Invoke-RestMethod -Uri http://localhost:8000/v1/models -TimeoutSec 10
.\.venv\Scripts\python.exe evaluation_runner.py --synthetic-count 3 --frame-count 1 --max-tokens 128 --timeout-sec 180
```

## 다음 실제 테스트 필요 항목

- 실제 mp4 3~5개
- 공개 YouTube URL 3~5개
- 긴 영상에서 frame count `4`, `6`, `8` 비교
- 한국어 fallback 비율이 synthetic 샘플과 실제 영상에서 어떻게 달라지는지 비교
