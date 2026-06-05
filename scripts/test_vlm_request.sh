#!/usr/bin/env bash
set -euo pipefail

MODEL_ID="${MODEL_ID:-Qwen/Qwen3-VL-2B-Instruct}"
PORT="${PORT:-8000}"
ENDPOINT="${ENDPOINT:-http://localhost:${PORT}/v1/chat/completions}"
IMAGE_URL="${IMAGE_URL:-https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/p-blog/candy.JPG}"
PROMPT="${PROMPT:-Describe this image in one sentence.}"

echo "최소 VLM 요청을 보냅니다"
echo "  엔드포인트: ${ENDPOINT}"
echo "  모델: ${MODEL_ID}"

# vLLM을 별도 alias로 실행하지 않았다면 model 필드는 서빙 중인 모델 ID와 일치해야 합니다.
# 이 요청은 OpenAI 호환 chat 엔드포인트와 image_url 페이로드 경로가 동작하는지 확인합니다.
curl -sS -X POST "${ENDPOINT}" \
  -H "Content-Type: application/json" \
  --data @- <<JSON
{
  "model": "${MODEL_ID}",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "${PROMPT}"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "${IMAGE_URL}"
          }
        }
      ]
    }
  ],
  "max_tokens": 128,
  "temperature": 0
}
JSON

echo
