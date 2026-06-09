# 개발 가이드

이 문서는 새 개발자가 프로젝트를 받아 로컬 PoC를 실행하는 순서를 정리합니다. 현재 기준은 Windows + Docker Desktop + NVIDIA GPU입니다.

## 1. 요구사항

| 항목 | 필요 조건 |
| --- | --- |
| OS | Windows |
| Python | 3.10 이상 권장 |
| Docker | Docker Desktop Linux Engine |
| GPU | NVIDIA GPU, `nvidia-smi` 동작 필요 |
| vLLM | Docker 이미지 `vllm/vllm-openai:latest` |

현재 검증된 GPU는 RTX 4070 Ti입니다. 다른 GPU는 VRAM 상황에 따라 `MAX_MODEL_LEN`, `GPU_MEMORY_UTILIZATION`, 샘플 프레임 수 조정이 필요할 수 있습니다.

## 2. 설치

```powershell
cd <전달받은_프로젝트_폴더>
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
```

`scripts/setup_venv.ps1`은 `.venv` 생성, `pip` 업데이트, `requirements.txt` 설치를 수행합니다.

## 3. Docker와 GPU 확인

```powershell
nvidia-smi
docker version
docker pull vllm/vllm-openai:latest
```

`nvidia-smi`가 실패하면 vLLM 문제가 아니라 NVIDIA 드라이버/GPU 환경 문제부터 확인합니다.

## 4. 앱 실행

```powershell
python app.py
```

기본 접속 주소는 `http://127.0.0.1:8080`입니다. 포트가 사용 중이면 앱이 다음 빈 포트를 자동으로 찾아 실행합니다.

다른 사용자가 같은 네트워크에서 이 PC로 접속해야 한다면 앱을 외부 접속용 host로 실행합니다.

```powershell
$env:APP_HOST="0.0.0.0"
$env:APP_PORT="8080"
python app.py
```

이 PC의 IPv4 주소를 확인합니다.

```powershell
ipconfig
```

다른 사용자는 브라우저에서 아래 형식으로 접속합니다.

```text
http://<이_PC의_IP주소>:8080
```

예: 이 PC의 IPv4 주소가 `192.168.0.25`이면 `http://192.168.0.25:8080`으로 접속합니다.

외부 PC에서 접속이 안 되면 Windows 방화벽에서 TCP `8080` 포트 인바운드 허용이 필요할 수 있습니다. vLLM `8000` 포트는 FastAPI 앱이 같은 PC에서 호출하므로 일반 사용자에게 직접 열 필요가 없습니다.

## 5. vLLM 시작

웹 화면 상단의 `vLLM 시작 / GPU 점유` 버튼을 누릅니다. 이 버튼은 내부적으로 Docker 컨테이너를 시작하고, 모델을 GPU에 로드합니다.

처음 실행 시 오래 걸릴 수 있는 단계:
- Docker 이미지 pull
- Hugging Face 모델 다운로드
- vLLM 모델 로딩
- 멀티모달 warmup

준비가 끝나면 `/v1/models`가 응답하고 화면의 worker 상태가 `ready`가 됩니다.

## 6. 영상 분석 테스트

1. 영상 파일을 업로드하거나 YouTube URL을 입력합니다.
2. 샘플 프레임 수는 기본 `6`으로 시작합니다.
3. `영상 분석 batch 생성` 버튼을 누릅니다.
4. 결과 영역에서 추출 프레임, VLM 응답, job 로그 경로를 확인합니다.

반복 테스트 기준:
- `frame_count=4`: 빠른 확인
- `frame_count=6`: 현재 기본값
- `frame_count=8`: 더 많은 장면을 보지만 처리시간이 늘고 품질이 항상 좋아지지는 않음

## 7. 테스트 후 정리

화면의 `임시파일 정리` 버튼 또는 아래 API를 사용합니다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
```

`dry_run=true`는 실제 삭제 없이 정리 대상만 확인합니다. 실제 정리는 화면 버튼 또는 `dry_run` 없는 호출로 수행합니다.

## 8. 기본 검증 명령

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py job_store.py prompt_utils.py runtime_utils.py worker_registry.py video_utils.py evaluation_runner.py
node --check static\app.js
git diff --check
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/config
Invoke-RestMethod -Uri http://127.0.0.1:8080/api/workers/refresh -Method Post
Invoke-RestMethod -Uri http://localhost:8000/v1/models
```

## 9. 현재 한계

- 로컬 검증은 단일 vLLM worker 기준입니다.
- Kubernetes time-slicing 실검증은 Linux/Kubernetes GPU node가 필요합니다.
- 실제 mp4 테스트 데이터는 아직 저장소에 포함되어 있지 않습니다.
- job 저장소는 파일 기반이므로 운영 DB가 아닙니다.
