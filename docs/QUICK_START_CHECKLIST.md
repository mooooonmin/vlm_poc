# 빠른 시작 체크리스트

## 1. 가상환경

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
```

확인 기준:

- `.venv` 폴더가 생성됩니다.
- `pip install -r requirements.txt`가 완료됩니다.

## 2. GPU와 Docker

```powershell
nvidia-smi
docker version
docker pull vllm/vllm-openai:latest
```

확인 기준:

- `nvidia-smi`에 NVIDIA GPU가 표시됩니다.
- Docker Desktop이 Linux Engine으로 동작합니다.
- vLLM 이미지가 로컬에 받아집니다.

## 3. 앱 실행

```powershell
python app.py
```

브라우저에서 `http://127.0.0.1:8080`에 접속합니다. 8080 포트가 사용 중이면 콘솔에 표시된 실제 포트로 접속합니다.

## 4. vLLM 시작

화면 오른쪽 런타임 영역에서 `vLLM 시작`을 누릅니다.

확인 기준:

- vLLM 상태가 `API ready`가 됩니다.
- worker 상태가 `ready`가 됩니다.

## 5. 영상 분석

1. `새 대화`를 누릅니다.
2. 오른쪽 영상 패널에서 파일 또는 YouTube URL을 등록합니다.
3. 중앙 채팅창에 질문을 입력합니다.
4. `질문 보내기`를 누릅니다.
5. 답변, 영상 미리보기, 추출 프레임을 확인합니다.

정상 기준:

- assistant 메시지가 `queued/running`에서 `done`으로 바뀝니다.
- 답변 카드가 표시됩니다.
- 오른쪽 패널에 추출 프레임이 표시됩니다.
- `tmp/jobs/{job_id}/job.json` 로그 경로가 표시됩니다.

## 6. 테스트 종료

테스트가 끝나면 `vLLM 종료`를 눌러 GPU 점유를 해제합니다.

임시파일 정리는 화면의 `임시파일 정리` 버튼을 사용합니다. 삭제 예정 항목만 확인하려면 다음 명령을 사용합니다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
```
