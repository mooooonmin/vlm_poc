# 개발 가이드

이 문서는 프로젝트를 받은 사용자가 로컬 PC에서 PoC를 실행하는 절차를 정리합니다. 기준 환경은 Windows + Docker Desktop + NVIDIA GPU입니다.

## 1. 요구사항

| 항목 | 필요 조건 |
| --- | --- |
| OS | Windows |
| Python | 3.10 이상 권장 |
| Docker | Docker Desktop Linux Engine |
| GPU | NVIDIA GPU, `nvidia-smi` 동작 필요 |
| vLLM 이미지 | `vllm/vllm-openai:latest` |

현재 검증된 GPU는 RTX 4070 Ti입니다. 다른 GPU에서는 VRAM 상황에 따라 `MAX_MODEL_LEN`, `GPU_MEMORY_UTILIZATION`, 프레임 수를 조정해야 할 수 있습니다.

## 2. 가상환경 설치

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
```

`setup_venv.ps1`는 `.venv` 생성, `pip` 업데이트, `requirements.txt` 설치를 수행합니다.

## 3. Docker와 GPU 확인

```powershell
nvidia-smi
docker version
docker pull vllm/vllm-openai:latest
```

`nvidia-smi`가 실패하면 vLLM 이전에 NVIDIA 드라이버와 GPU 환경부터 확인해야 합니다.

## 4. 앱 실행

```powershell
python app.py
```

기본 주소는 `http://127.0.0.1:8080`입니다. 8080 포트가 사용 중이면 앱이 다음 빈 포트를 자동으로 찾아 실행합니다.

다른 사용자가 같은 네트워크에서 이 PC로 접속해야 하면 다음처럼 실행합니다.

```powershell
$env:APP_HOST="0.0.0.0"
$env:APP_PORT="8080"
python app.py
```

PC의 IPv4 주소는 `ipconfig`로 확인합니다. 다른 사용자는 `http://<PC의 IPv4 주소>:8080` 형식으로 접속합니다. Windows 방화벽에서 TCP 8080 인바운드 허용이 필요할 수 있습니다.

## 5. vLLM 시작

화면 오른쪽 런타임 영역에서 `vLLM 시작`을 누릅니다. 이 버튼은 Docker 컨테이너를 시작하고 모델을 GPU에 로드합니다.

처음 실행할 때 오래 걸릴 수 있는 단계:

- Docker 이미지 pull
- Hugging Face 모델 다운로드
- vLLM 모델 로딩
- `/v1/models` ready 대기

준비가 끝나면 worker 상태가 `ready`로 표시됩니다.

## 6. 영상 분석 테스트

1. `새 대화`를 누릅니다.
2. 오른쪽 영상 패널에서 영상 파일 또는 YouTube URL을 등록합니다.
3. 샘플링 방식, 최대 프레임 수, 최대 토큰을 확인합니다.
4. 중앙 채팅창에 질문을 입력하고 `질문 보내기`를 누릅니다.
5. 답변, 원본 영상 미리보기, 추출 프레임, job 로그 경로를 확인합니다.

기본 설정:

| 항목 | 기본값 |
| --- | --- |
| 샘플링 방식 | `segment` |
| 최대 프레임 수 | `30` |
| 최대 토큰 | `1024` |
| 모델 | `Qwen/Qwen3-VL-2B-Instruct` |

## 7. 임시파일 정리

화면의 `임시파일 정리` 버튼을 사용하거나 다음 API를 호출합니다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
```

`dry_run=true`는 실제 삭제 없이 삭제 예정 항목만 계산합니다. 실제 정리는 버튼 또는 `dry_run` 없는 API 호출로 수행합니다.

## 8. 기본 검증 명령

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py job_store.py conversation_store.py prompt_utils.py runtime_utils.py worker_registry.py video_utils.py evaluation_runner.py
node --check static\app.js
git diff --check
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/config
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/workers/refresh -Method Post
Invoke-RestMethod -Uri http://localhost:8000/v1/models
```

## 9. 현재 제한

| 항목 | 내용 |
| --- | --- |
| 대화당 영상 수 | 1개 |
| 실제 병렬 처리 | 로컬 기본값은 worker 1개 |
| Kubernetes time-slicing | 로컬 Windows에서는 미적용 |
| 운영 저장소 | DB가 아닌 파일 기반 |
