# 가상환경 준비 후 앱 실행.  사용: .\scripts\dev.ps1 [--debug]
. "$PSScriptRoot\_venv.ps1"
& $Py -m vcam @args
exit $LASTEXITCODE
