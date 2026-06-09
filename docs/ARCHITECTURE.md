# 아키텍처 개요

이 PoC는 FastAPI 앱, 파일 기반 대화/job 저장소, Docker vLLM 서버로 구성됩니다.

## 처리 흐름

```text
브라우저
  -> 대화 생성
  -> 영상 파일 또는 URL 등록
  -> 질문 전송
  -> conversation API가 내부 job 생성
  -> dispatcher가 ready vLLM worker에 job 배정
  -> OpenCV 프레임 추출
  -> vLLM OpenAI 호환 API 호출
  -> job.json 저장
  -> conversation 메시지에 답변 상태 반영
  -> 화면 polling으로 채팅 답변 갱신
```

## 주요 구성

| 구성 | 역할 |
| --- | --- |
| `app.py` | FastAPI 서버, conversation API, job API, dispatcher |
| `conversation_store.py` | 채팅 세션과 메시지를 `tmp/conversations`에 저장 |
| `job_store.py` | 분석 job 상태, 결과, 로그 경로, 임시파일 정리 |
| `video_utils.py` | 업로드 저장, YouTube/URL 다운로드, OpenCV 프레임 추출 |
| `prompt_utils.py` | 내부 프롬프트, vLLM payload, 답변 후처리 |
| `runtime_utils.py` | CUDA 확인, Docker vLLM 시작/종료, time-slicing 로그 |
| `worker_registry.py` | vLLM worker readiness와 job 배정 상태 |
| `templates/index.html` | 채팅형 관제 UI HTML |
| `static/app.js` | 대화 목록, 영상 등록, 질문 전송, polling |
| `static/style.css` | 밝은 관제형 4영역 레이아웃 |

## 저장 경로

| 경로 | 내용 |
| --- | --- |
| `tmp/conversations/{conversation_id}/conversation.json` | 대화 제목, 영상 source, 메시지, 연결 job 목록 |
| `tmp/jobs/{job_id}/job.json` | 프레임, 답변, 실패 원인, worker, 처리 시간 |
| `tmp/frames/` | 화면 미리보기와 vLLM 요청에 사용한 JPEG 프레임 |
| `logs/timeslicing/{run_id}/` | Kubernetes time-slicing 검증 리포트 |
| `logs/evaluation/{run_id}/` | 평가 실행 리포트 |

## conversation과 job의 관계

대화 세션은 사용자 경험 단위입니다. 한 대화에는 영상 1개만 연결합니다.

job은 실제 분석 실행 단위입니다. 같은 대화에서 질문을 여러 번 보내면 질문마다 새로운 job이 생성되고, assistant 메시지의 `job_id`로 연결됩니다.

이 구조를 쓰는 이유:

- 같은 영상에 대해 여러 질문을 이어서 테스트할 수 있습니다.
- 질문별 실패 원인과 프레임 근거를 job 단위로 분리해 볼 수 있습니다.
- 기존 `/api/jobs/*` 흐름을 유지하면서 새 채팅 UI를 추가할 수 있습니다.

## vLLM worker 구조

로컬 기본값은 `http://localhost:8000/v1/chat/completions` worker 1개입니다. `VLLM_WORKERS` 환경변수에 여러 endpoint를 넣으면 dispatcher가 ready 상태 worker에 job을 배정할 수 있습니다.

Kubernetes time-slicing은 worker를 자동으로 나누지 않습니다. time-slicing은 GPU 접근 슬롯을 oversubscribe하는 클러스터 설정이고, 어떤 요청을 어느 vLLM endpoint로 보낼지는 이 앱의 dispatcher가 담당합니다.

## 현재 한계

| 항목 | 상태 |
| --- | --- |
| 운영 DB | 미사용. 파일 기반 저장소 |
| 사용자 인증 | 미구현 |
| 실시간 스트림/RTSP | 미구현 |
| 다중 영상 비교 | 미구현. 한 대화는 영상 1개 기준 |
| Kubernetes time-slicing 실검증 | Linux/K8s GPU node 필요 |
