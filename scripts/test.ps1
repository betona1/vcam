# Ruff + pytest.  사용: .\scripts\test.ps1 [-Fast]   (-Fast: 실제 화면 캡처 스모크 테스트 제외)
param([switch]$Fast)
. "$PSScriptRoot\_venv.ps1"
Push-Location $Root
try {
    & $Py -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $marker = if ($Fast) { @("-m", "not smoke") } else { @() }
    & $Py -m pytest -q -p no:cacheprovider @marker
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
