# PyInstaller one-folder 빌드 → <Dist>\vcam\vcam.exe 와 배포용 <Dist>\vcam-<버전>-win64.zip(+.sha256)
# 사용: .\scripts\build.ps1 [-Dist 경로]
# FFmpeg는 번들하지 않는다(코덱 라이선스 검토 전). 사용자는 winget 등으로 설치하거나 설정에서 경로를 지정한다.
param([string]$Dist = "")
. "$PSScriptRoot\_venv.ps1"
if (-not $Dist) {
    # 네트워크 드라이브에서는 exe 실행이 막히므로 로컬 디스크에 빌드한다.
    $onNetwork = ([System.IO.DriveInfo]::new($Root)).DriveType -eq "Network"
    $Dist = if ($onNetwork) { Join-Path $env:LOCALAPPDATA "vcam-dev\dist" } else { Join-Path $Root "dist" }
}
if (-not [System.IO.Path]::IsPathRooted($Dist)) { $Dist = Join-Path $Root $Dist }
$Dist = [System.IO.Path]::GetFullPath($Dist)
$Work = Join-Path (Split-Path $Dist) "build"
Push-Location $Root
try {
    & $Py scripts\make_icon.py
    & $Py -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name vcam `
        --icon "$Root\assets\icons\vcam.ico" `
        --add-data "$Root\assets;assets" `
        --add-data "$Root\THIRD_PARTY_NOTICES.md;." `
        --add-data "$Root\LICENSE;." `
        --collect-submodules dxcam `
        --hidden-import comtypes.stream `
        --paths "$Root\src" `
        --distpath $Dist --workpath $Work --specpath $Work `
        "$Root\src\vcam\__main__.py"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "빌드 완료: $Dist\vcam\vcam.exe"

    # 배포 zip(최상위 폴더 vcam\)과 SHA-256 체크섬 — 자동 업데이트가 이 두 파일을 사용한다.
    $Version = & $Py -c "import vcam; print(vcam.__version__)"
    $Zip = Join-Path $Dist "vcam-$Version-win64.zip"
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Compress-Archive -Path (Join-Path $Dist "vcam") -DestinationPath $Zip -CompressionLevel Optimal
    $Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
    [System.IO.File]::WriteAllText("$Zip.sha256", "$Hash  $(Split-Path $Zip -Leaf)`n")
    Write-Host "배포 파일: $Zip"
} finally {
    Pop-Location
}
