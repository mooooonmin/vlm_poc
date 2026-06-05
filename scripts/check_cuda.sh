#!/usr/bin/env bash
set -euo pipefail

# 이 스크립트는 의도적으로 최소 점검만 수행합니다. 모든 점검이 통과하기 전에는 vLLM을 실행하지 않습니다.

echo "[1/3] 호스트 NVIDIA 드라이버가 nvidia-smi로 보이는지 확인합니다..."
# Docker/CUDA 런타임을 보기 전에, 먼저 호스트 NVIDIA 드라이버 스택이 정상이어야 합니다.
nvidia-smi

echo
echo "[2/3] Docker CLI 사용 가능 여부를 확인합니다..."
# 이 서빙 초안은 공식 vLLM Docker 이미지를 사용하므로 Docker가 먼저 설치되어 있어야 합니다.
docker version

echo
echo "[3/3] NVIDIA Container Toolkit을 통한 컨테이너 GPU 접근을 확인합니다..."
# Docker가 GPU를 컨테이너로 전달할 수 있는지 확인합니다. 실패하면 NVIDIA Container Toolkit 설치 또는 설정을 먼저 수정해야 합니다.
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

echo
echo "CUDA와 Docker GPU 점검이 통과했습니다. vLLM 서빙 단계로 진행할 수 있습니다."
