# 문제 해결 가이드

## 8080 포트가 이미 사용 중

앱은 `APP_PORT`부터 시작해 다음 빈 포트를 찾습니다. 실제 접속 주소는 `python app.py` 콘솔 출력에서 확인합니다.

원하는 포트로 바꾸려면:

```powershell
$env:APP_PORT="8090"
python app.py
```

## Docker가 응답하지 않음

확인:

```powershell
docker version
docker ps
```

Docker Desktop이 실행 중인지, Linux Engine이 활성화되어 있는지 확인합니다.

## GPU가 인식되지 않음

확인:

```powershell
nvidia-smi
```

`nvidia-smi`가 실패하면 NVIDIA 드라이버, Windows/WSL2 GPU 지원, Docker Desktop GPU 접근 설정을 먼저 확인합니다.

## vLLM 이미지가 없음

처음 실행 전 이미지를 먼저 받습니다.

```powershell
docker pull vllm/vllm-openai:latest
```

이미지가 없으면 화면에서 vLLM 시작 시 pull 단계 때문에 오래 걸릴 수 있습니다.

## vLLM 시작이 오래 걸림

정상적으로 오래 걸릴 수 있는 단계:
- Docker image pull
- Hugging Face 모델 다운로드
- 모델 weight 로딩
- CUDA graph 준비
- 멀티모달 warmup

화면의 `vLLM 로그` 버튼 또는 아래 명령으로 확인합니다.

```powershell
docker logs --tail 120 vlm-vllm-qwen
```

## vLLM OOM 또는 GPU 메모리 부족

우선 아래 순서로 낮춰 테스트합니다.

1. 샘플 프레임 수를 `4`로 낮춤
2. `MAX_MODEL_LEN=4096`
3. `GPU_MEMORY_UTILIZATION=0.80`

예:

```powershell
$env:MAX_MODEL_LEN="4096"
$env:GPU_MEMORY_UTILIZATION="0.80"
python app.py
```

이미 떠 있는 vLLM 컨테이너는 종료 후 다시 시작해야 새 설정이 반영됩니다.

## YouTube 다운로드 실패

가능한 원인:
- 비공개 영상
- 연령 제한
- 지역 제한
- 네트워크 차단
- `yt-dlp`가 해당 URL을 처리하지 못함

확인은 job 결과의 실패 메시지와 `tmp/jobs/{job_id}/job.json`을 봅니다.

## OpenCV가 영상을 열 수 없음

가능한 원인:
- 다운로드된 파일이 실제 영상이 아님
- 코덱이 OpenCV와 맞지 않음
- 파일이 손상됨
- 영상 길이 또는 파일 크기 제한 초과

먼저 짧은 mp4 파일로 파일 업로드 테스트를 진행합니다.

## 응답이 한국어가 아니거나 반복됨

현재 앱은 한국어 응답 검사를 수행하고 실패 시 재요청/정리 단계를 거칩니다. 그래도 품질 이슈가 있으면 다음을 줄여 테스트합니다.

- 샘플 프레임 수
- 최대 토큰
- 분석 요청 문장 길이

반복적으로 발생하는 패턴은 `docs/TEST_RESULTS.md`에 기록한 뒤 `prompt_utils.py`에서 최소 보정합니다.

## 원본 JSON을 보고 싶음

화면에는 원본 JSON을 표시하지 않습니다. 아래 파일에서 확인합니다.

```text
tmp/jobs/{job_id}/job.json
```

## 임시파일이 계속 쌓임

화면의 `임시파일 정리` 버튼을 사용합니다. 실제 삭제 전 대상만 확인하려면:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/tmp/cleanup?dry_run=true' -Method Post
```
