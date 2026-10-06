# 공통: 가상환경 위치 결정과 준비.
# 저장소가 네트워크 드라이브에 있으면 그 안의 python.exe 실행이 막히므로 기본 위치를 로컬 디스크로 둔다.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = if ($env:VCAM_VENV) { $env:VCAM_VENV } else { Join-Path $env:LOCALAPPDATA "vcam-dev\venv" }
$Py = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Host "가상환경 생성: $Venv"
    # VCAM_PYTHON으로 기준 파이썬을 지정할 수 있다(CI). 없으면 py 런처의 최신 Python 3.
    # @(...)로 감싸야 한다: if 식은 원소 1개짜리 배열을 문자열로 풀어 버린다.
    $Base = @(if ($env:VCAM_PYTHON) { $env:VCAM_PYTHON } else { "py", "-3" })
    & $Base[0] $Base[1..9] -c "import sys; assert sys.version_info >= (3, 12), sys.version" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 이상이 필요합니다. 'py -0p'로 설치된 버전을 확인해 주세요." }
    & $Base[0] $Base[1..9] -m venv $Venv
}

# pyproject.toml이 바뀌었을 때만 의존성을 다시 설치한다.
$Stamp = Join-Path $Venv ".vcam-deps"
$Hash = (Get-FileHash (Join-Path $Root "pyproject.toml")).Hash
if (-not (Test-Path $Stamp) -or (Get-Content $Stamp) -ne $Hash) {
    & $Py -m pip install -q --upgrade pip
    & $Py -m pip install -q -e "$Root[dev]"
    if ($LASTEXITCODE -ne 0) { throw "의존성 설치 실패" }
    Set-Content -Path $Stamp -Value $Hash
}
