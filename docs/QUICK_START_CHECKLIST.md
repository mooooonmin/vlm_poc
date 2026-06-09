# 빠른 실행 체크리스트

이 문서는 프로젝트를 처음 받은 사용자가 로컬에서 PoC 화면을 띄우고 영상 분석까지 확인하는 순서입니다. 자세한 설명은 `docs/DEVELOPMENT_GUIDE.md`, `docs/DEPLOYMENT_GUIDE.md`, `docs/TROUBLESHOOTING.md`를 봅니다.

## 1. 설치 전 확인

| 확인 항목 | 명령/확인 방법 | 정상 기준 |
| --- | --- | --- |
| Python | `python --version` | Python 3.10 이상 권장 |
| NVIDIA GPU | `nvidia-smi` | GPU 이름과 VRAM 정보 표시 |
| Docker Desktop | `docker version` | Client/Server 정보 표시 |

`nvidia-smi` 또는 `docker version`이 실패하면 앱 실행 전에 환경부터 해결합니다.

## 2. 프로젝트 폴더로 이동

```powershell
cd <전달받은_프로젝트_폴더>
```

프로젝트는 별도로 전달받는 것을 기준으로 합니다. 위 명령에서 `<전달받은_프로젝트_폴더>`는 실제 압축 해제 또는 전달받은 폴더 경로로 바꿉니다.

## 3. Python 가상환경 준비

```powershell
.\scripts\setup_venv.ps1
.\.venv\Scripts\Activate.ps1
```

정상 기준:
- `.venv` 폴더가 생성됨
- `pip install -r requirements.txt`가 완료됨
- PowerShell 프롬프트 앞에 `(.venv)`가 표시됨

PowerShell 실행 정책 오류가 나면 현재 터미널에서만 아래 명령을 실행한 뒤 다시 시도합니다.

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

## 4. vLLM Docker 이미지 받기

```powershell
docker pull vllm/vllm-openai:latest
```

정상 기준:
- 이미지 다운로드가 완료됨
- 이미 받은 이미지면 `Image is up to date` 계열 메시지가 표시됨

## 5. 앱 실행

이 PC에서만 테스트할 때:

```powershell
python app.py
```

다른 사용자가 같은 네트워크에서 접속해야 할 때:

```powershell
$env:APP_HOST="0.0.0.0"
$env:APP_PORT="8080"
python app.py
```

정상 기준:
- 콘솔에 Uvicorn 서버 시작 로그가 표시됨
- 기본 주소는 `http://127.0.0.1:8080`
- 다른 PC 접속 주소는 `http://<이_PC의_IP주소>:8080`

이 PC의 IP 확인:

```powershell
ipconfig
```

## 6. 웹 화면 접속

브라우저에서 접속합니다.

```text
http://127.0.0.1:8080
```

다른 PC에서는 예를 들어 아래처럼 접속합니다.

```text
http://192.168.0.25:8080
```

접속이 안 되면:
- 앱을 `APP_HOST=0.0.0.0`으로 실행했는지 확인
- Windows 방화벽에서 TCP `8080` 인바운드 허용 확인
- 같은 네트워크에 있는지 확인

## 7. vLLM 시작

웹 화면 상단에서 `vLLM 시작 / GPU 점유` 버튼을 누릅니다.

처음 실행 시 오래 걸릴 수 있습니다.
- Docker 이미지 확인
- Hugging Face 모델 다운로드
- 모델 GPU 로딩
- 멀티모달 warmup

정상 기준:
- 화면 상태가 `API ready` 또는 worker `ready`로 바뀜
- 아래 명령이 모델 목록을 반환함

```powershell
Invoke-RestMethod -Uri http://localhost:8000/v1/models
```

## 8. 영상 분석 테스트

1. 영상 파일을 선택하거나 YouTube URL을 입력합니다.
2. 샘플링 방식은 기본 `구간 프레임`, 최대 프레임 수는 기본 `30`으로 둡니다.
3. `영상 분석 batch 생성` 버튼을 누릅니다.
4. 추출 프레임과 VLM 응답이 표시되는지 확인합니다.

정상 기준:
- job 상태가 `done`으로 끝남
- 영상 미리보기가 표시됨
- 추출 프레임 카드가 표시됨
- VLM 응답이 표시됨
- `tmp/jobs/{job_id}/job.json`이 생성됨

## 9. 종료와 정리

테스트가 끝나면 화면의 `vLLM 종료 / GPU 해제` 버튼으로 GPU 점유를 해제합니다.

임시파일 정리 전 대상 확인:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
```

실제 정리는 화면의 `임시파일 정리` 버튼을 사용합니다.

## 10. 막혔을 때 보는 문서

| 상황 | 볼 문서 |
| --- | --- |
| 설치와 실행 순서가 필요함 | `docs/DEVELOPMENT_GUIDE.md` |
| Docker/vLLM/Kubernetes 서빙 절차가 필요함 | `docs/DEPLOYMENT_GUIDE.md` |
| 환경변수 의미를 알고 싶음 | `docs/CONFIGURATION.md` |
| 구조를 이해하고 싶음 | `docs/ARCHITECTURE.md` |
| 오류가 발생함 | `docs/TROUBLESHOOTING.md` |
| 검증 결과를 보고 싶음 | `docs/TEST_RESULTS.md` |

## 11. 현재 기준 주의사항

- 로컬 기본 검증은 단일 vLLM worker 기준입니다.
- vLLM `8000` 포트는 FastAPI 앱 내부 호출용입니다. 일반 테스트 사용자는 FastAPI `8080`으로 접속합니다.
- 실제 mp4 테스트 데이터는 저장소에 포함되어 있지 않습니다.
- Kubernetes time-slicing 실검증은 Linux/Kubernetes GPU node가 필요합니다.
