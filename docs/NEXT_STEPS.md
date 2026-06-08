# 다음 진행 작업

현재 PoC는 로컬 Docker/vLLM 단일 worker 기준으로 영상 분석, job 로그, 평가 러너, 평가 리포트 UI까지 구현되어 있습니다.

## 1. 실제 샘플 평가

목표: synthetic 영상이 아니라 실제 사용 시나리오에 가까운 샘플로 성공률과 fallback 비율을 확인합니다.

- 짧은 mp4 3~5개를 준비합니다.
- 공개 YouTube URL 3~5개를 준비합니다.
- `evaluation_runner.py --samples samples.json`으로 순차 평가합니다.
- 결과는 `logs/evaluation/{run_id}/summary.json`, `summary.md`에 저장합니다.
- 테스트 결과 요약은 `docs/TEST_RESULTS.md`에 추가합니다.

## 2. 한국어 응답 품질 튜닝

목표: fallback 비율을 낮추고, 실제 분석 응답을 더 안정적으로 한국어화합니다.

- 실제 샘플 기준 `korean_fallback_rate`를 확인합니다.
- 필요하면 `KOREAN_MIN_HANGUL`, `KOREAN_MIN_RATIO`를 조정합니다.
- 프롬프트를 관제/상황 요약 목적에 맞게 더 좁힙니다.
- fallback이 발생한 원문 응답을 모아 모델이 지시를 따르지 않는 패턴을 정리합니다.

## 3. 프레임/토큰 기본값 튜닝

목표: RTX 4070 Ti에서 안정적으로 반복 실행 가능한 기본값을 잡습니다.

- `frame_count=4`, `6`, `8`을 비교합니다.
- `max_tokens=256`, `512`를 비교합니다.
- 평균 처리시간, 실패율, fallback 비율을 기록합니다.
- OOM이 발생하면 `MAX_MODEL_LEN=4096`, 샘플 프레임 `4` 기준으로 재검증합니다.

## 4. 다중 worker 실험

목표: `VLLM_WORKERS` 구조가 실제 다중 endpoint에서도 의미 있게 동작하는지 확인합니다.

- 로컬 RTX 4070 Ti 12GB에서는 vLLM 2개 동시 로딩이 실패할 수 있습니다.
- 먼저 `worker-1 ready`, `worker-2 error` 상태 검증은 완료됐습니다.
- 실제 2개 worker는 별도 GPU 서버 또는 Kubernetes GPU node에서 검증합니다.
- Kubernetes time-slicing 실검증은 `k8s/README.md` 절차를 따릅니다.

## 5. Kubernetes Time-slicing 실검증

목표: NVIDIA device-plugin time-slicing 설정이 GPU node에서 실제로 적용되는지 확인합니다.

- `kubectl` context가 Linux/K8s GPU node에 연결되어야 합니다.
- NVIDIA device-plugin ConfigMap을 적용합니다.
- `kubectl describe nodes`에서 `nvidia.com/gpu` 리소스 노출 상태를 확인합니다.
- vLLM Pod `replicas: 2` 스케줄링과 `/v1/models` 응답을 확인합니다.
