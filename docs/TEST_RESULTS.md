# 테스트 결과 기록

이 문서는 사람이 확인할 최종 검증 기록만 남깁니다. 자동 생성 상세 로그는 `logs/`와 `tmp/jobs/{job_id}/job.json`에 저장되며 임시파일 정리 대상입니다.

## 기준 환경

| 항목 | 확인 결과 | 근거 |
| --- | --- | --- |
| GPU | NVIDIA GeForce RTX 4070 Ti | `nvidia-smi` |
| Docker | Docker Desktop Linux Engine | `docker version` |
| vLLM | `vllm/vllm-openai:latest`, 기본 포트 8000 | `/api/vllm-status`, `/v1/models` |
| 기본 모델 | `Qwen/Qwen3-VL-2B-Instruct` | `/api/config` |
| Kubernetes | 로컬 context 미연결 또는 별도 검증 필요 | `/api/timeslicing/logs` |

## 최근 검증

| 일시 | 범위 | 입력/조건 | 결과 | 비고 |
| --- | --- | --- | --- | --- |
| 2026-06-09 | 정적 검증 | `py_compile`, `node --check`, `git diff --check` | 성공 | `conversation_store.py` 포함 |
| 2026-06-09 | Conversation API | 대화 생성, 목록 조회, 영상 URL 등록 | 성공 | 임시 서버 `127.0.0.1:8099`에서 HTTP 검증 |
| 2026-06-09 | Conversation API | 영상 미등록 상태 질문 전송 | 성공 | `400`, “먼저 영상을 등록하세요.” 반환 확인 |
| 2026-06-09 | UI 렌더링 | 채팅형 4영역 화면 로드 | 성공 | 브라우저 DOM 확인, 콘솔 오류 없음 |
| 2026-06-09 | UI 상호작용 | `새 대화` 버튼 | 성공 | 새 대화 생성, empty state 표시 |
| 2026-06-10 | UI/UX 정리 | 미리보기 우선 영상 패널, 상태 배지, 질문 버튼 상태 | 성공 | 영상 등록 후 질문 버튼 활성화, 콘솔 오류 없음 |

## 이전 주요 검증 요약

| 범위 | 결과 |
| --- | --- |
| YouTube URL 입력 | 공개 YouTube URL 분석 성공 사례 확인 |
| 영상 미리보기 | `/api/jobs/{job_id}/video`로 원본 영상 미리보기 확인 |
| 프레임 추출 | `segment`, `one_fps` 방식 확인 |
| 프레임 확대 | 추출 프레임 클릭 시 모달 확대 확인 |
| vLLM context 초과 대응 | 추출 프레임 수와 vLLM 전송 프레임 수 분리, 축소 재시도 확인 |
| vLLM 500 대응 | 중복 프레임 제거와 축소 재시도 로직 추가 |
| Time-slicing 로그 | 로컬 미연결 Kubernetes 환경에서 `not_available`/원인 코드 기록 확인 |

## 남은 실검증

| 항목 | 상태 |
| --- | --- |
| 실제 mp4 3~5개 기준 반복 테스트 | 미완료 |
| Linux/Kubernetes GPU node time-slicing 적용 | 미완료 |
| vLLM Pod 2개 이상 + `VLLM_WORKERS` 다중 endpoint 검증 | 미완료 |
| 장시간 반복 테스트 후 임시파일 정리와 로그 용량 확인 | 미완료 |
