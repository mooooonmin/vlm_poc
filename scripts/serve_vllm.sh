#!/usr/bin/env bash
set -euo pipefail

# 기본 모델은 추천 후보 중 가장 가벼운 상업 사용 가능 Qwen VLM입니다.
MODEL_ID="${MODEL_ID:-Qwen/Qwen3-VL-2B-Instruct}"
PORT="${PORT:-8000}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.85}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-8192}"
HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
CONTAINER_NAME="${CONTAINER_NAME:-vlm-vllm-qwen}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"

mkdir -p "${HF_HOME}"

echo "vLLM OpenAI 호환 서버를 시작합니다"
echo "  모델: ${MODEL_ID}"
echo "  포트: ${PORT}"
echo "  gpu_memory_utilization: ${GPU_MEMORY_UTILIZATION}"
echo "  max_model_len: ${MAX_MODEL_LEN}"
echo "  캐시: ${HF_HOME}"

# 반복 테스트 중 컨테이너 이름이나 포트 충돌을 피하기 위해 같은 이름의 이전 컨테이너를 제거합니다.
if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

HF_TOKEN_ARGS=()
if [[ -n "${HF_TOKEN:-}" ]]; then
  # 일부 gated 모델이나 다운로드 제한 상황에서는 Hugging Face 토큰이 필요할 수 있습니다.
  HF_TOKEN_ARGS=(-e "HF_TOKEN=${HF_TOKEN}")
fi

# --ipc=host는 PyTorch/vLLM 워크로드에서 공유 메모리 문제를 피하기 위해 자주 필요합니다.
# 공식 vLLM Docker 문서는 vllm/vllm-openai 이미지에 --model 인자를 사용하는 예시를 제공합니다.
docker run --rm \
  --name "${CONTAINER_NAME}" \
  --gpus all \
  --ipc=host \
  -p "${PORT}:8000" \
  -v "${HF_HOME}:/root/.cache/huggingface" \
  "${HF_TOKEN_ARGS[@]}" \
  "${VLLM_IMAGE}" \
  --model "${MODEL_ID}" \
  --host 0.0.0.0 \
  --port 8000 \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --trust-remote-code
