#!/usr/bin/env bash
set -euo pipefail

# Linux GPU 서버 기준 가상환경 생성 스크립트입니다.
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PATH="${VENV_PATH:-.venv}"

# 스크립트 위치와 관계없이 프로젝트 루트에서 실행되도록 이동합니다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# 사용할 Python 버전을 출력해 실행 환경을 기록하기 쉽게 합니다.
"${PYTHON_BIN}" --version

# .venv가 없을 때만 생성합니다. 이미 있으면 기존 환경을 유지합니다.
if [[ ! -d "${VENV_PATH}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_PATH}"
fi

# 가상환경 안의 Python으로 pip와 requirements를 설치합니다.
"${VENV_PATH}/bin/python" -m pip install --upgrade pip
"${VENV_PATH}/bin/python" -m pip install -r requirements.txt

echo
echo "가상환경 준비 완료"
echo "활성화: source .venv/bin/activate"
echo "실행:   python app.py"
