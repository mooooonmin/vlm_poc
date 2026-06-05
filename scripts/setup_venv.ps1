param(
    [string]$Python = "python",
    [string]$VenvPath = ".venv"
)

# PowerShell 오류를 즉시 중단해서 가상환경 생성 실패를 숨기지 않습니다.
$ErrorActionPreference = "Stop"

# 한글 안내 문구가 깨지지 않도록 콘솔 출력 인코딩을 UTF-8로 맞춥니다.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 프로젝트 루트 기준으로 실행되도록 현재 스크립트 위치에서 상위 폴더로 이동합니다.
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

# 지정된 Python 실행 파일이 실제로 사용 가능한지 확인합니다.
& $Python --version

# .venv가 없을 때만 새로 생성합니다. 이미 있으면 기존 환경을 재사용합니다.
if (-not (Test-Path $VenvPath)) {
    & $Python -m venv $VenvPath
}

# Windows PowerShell 기준 가상환경 Python 경로입니다.
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

# pip 자체를 최신화합니다.
& $VenvPython -m pip install --upgrade pip

# requirements.txt는 현재 비어 있지만, 향후 의존성 추가를 대비해 항상 설치 절차를 거칩니다.
& $VenvPython -m pip install -r requirements.txt

Write-Host ""
Write-Host "가상환경 준비 완료"
Write-Host "활성화: .\.venv\Scripts\Activate.ps1"
Write-Host "실행:   python app.py"
