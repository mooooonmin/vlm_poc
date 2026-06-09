# 아키텍처 개요

이 PoC는 FastAPI 앱, Docker vLLM 서버, 파일 기반 job 저장소로 구성됩니다.

## 전체 흐름

```text
브라우저
  -> FastAPI app.py
  -> 영상 저장 또는 YouTube 다운로드
  -> OpenCV 프레임 균등 샘플링
  -> 프레임 JPEG base64 변환
  -> vLLM OpenAI 호환 API 호출
  -> 응답 후처리
  -> tmp/jobs/{job_id}/job.json 저장
  -> 화면에 영상 미리보기, 프레임, 답변 표시
```

## 주요 구성

| 구성 | 역할 |
| --- | --- |
| `app.py` | FastAPI 서버, job 생성, dispatcher, API 라우트 |
| `video_utils.py` | 업로드 저장, URL 다운로드, OpenCV 프레임 샘플링 |
| `prompt_utils.py` | 질문 유형 분류, vLLM payload 생성, 응답 후처리 |
| `runtime_utils.py` | CUDA 확인, Docker vLLM 시작/종료, time-slicing 로그 |
| `worker_registry.py` | vLLM worker readiness와 job 배정 상태 관리 |
| `job_store.py` | job 상태 저장, 통계, 임시파일 정리 |
| `templates/`, `static/` | 테스트 UI |

## job 처리 방식

분석 요청은 즉시 최종 결과를 반환하지 않고 job으로 등록됩니다.

1. API가 job을 생성하고 `queued` 상태로 저장합니다.
2. dispatcher thread가 ready 상태의 vLLM worker를 찾습니다.
3. worker가 배정되면 job은 `running` 상태가 됩니다.
4. 프레임 추출과 vLLM 요청이 끝나면 `done` 또는 `failed`로 바뀝니다.
5. 화면은 job 또는 batch API를 polling해 상태를 갱신합니다.

## 저장 경로

| 경로 | 내용 |
| --- | --- |
| `tmp/jobs/{job_id}/` | 업로드/다운로드 영상, `job.json` |
| `tmp/frames/` | 화면 미리보기와 vLLM 요청에 사용한 추출 프레임 |
| `logs/evaluation/{run_id}/` | 평가 러너 리포트 |
| `logs/timeslicing/{run_id}/` | Kubernetes/time-slicing 검증 리포트 |

`tmp/`와 `logs/`의 자동 생성 파일은 임시파일 정리 대상입니다. `docs/TEST_RESULTS.md`는 사람이 관리하는 검증 문서라 삭제 대상이 아닙니다.

## 영상 미리보기

업로드 파일과 URL에서 다운로드된 영상은 `tmp/jobs/{job_id}/input.*` 형태로 저장됩니다. 화면은 `/api/jobs/{job_id}/video`를 통해 해당 job 폴더 안의 영상만 미리보기로 표시합니다. `tmp` 전체를 정적 경로로 공개하지 않고, job에 기록된 영상 경로가 해당 job 폴더 내부일 때만 반환합니다.

## vLLM 사용 방식

FastAPI 앱은 모델을 직접 로드하지 않습니다. 화면의 `vLLM 시작 / GPU 점유` 버튼은 Docker 컨테이너를 시작하고, 앱은 `VLLM_ENDPOINT`로 HTTP 요청을 보냅니다.

이렇게 분리하는 이유:
- 앱 서버 오류와 GPU 추론 서버 오류를 구분하기 쉽습니다.
- vLLM 로그를 Docker 로그로 따로 확인할 수 있습니다.
- 향후 여러 vLLM endpoint를 worker로 등록할 수 있습니다.

## time-slicing 위치

time-slicing은 로컬 Windows 기능이 아닙니다. Kubernetes GPU node에서 NVIDIA device-plugin이 GPU 리소스를 oversubscribe하도록 설정하는 영역입니다.

현재 저장소의 범위:
- `k8s/`에 적용 초안 제공
- `/api/timeslicing/logs`로 검증 로그 수집
- `VLLM_WORKERS`로 여러 vLLM endpoint 등록 준비

아직 완료되지 않은 범위:
- 실제 Linux/Kubernetes GPU node에서 time-slicing 적용
- vLLM Pod 2개 이상 실부하 검증
- GPU OOM/처리량 기준 확정
