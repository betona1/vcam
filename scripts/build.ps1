# PyInstaller one-folder 빌드 → <Dist>\vcam\vcam.exe 와 배포용 <Dist>\vcam-<버전>-win64.zip(+.sha256)
# 사용: .\scripts\build.ps1 [-Dist 경로]
# FFmpeg는 번들하지 않는다(코덱 라이선스 검토 전). 사용자는 winget 등으로 설치하거나 설정에서 경로를 지정한다.
# PyAV(faster-whisper 의존성)도 넣지 않는다: 동봉 FFmpeg DLL이 GPL 빌드라서. 대신 pyi_rth_no_pyav.py가 빈 모듈을 등록한다.
# yt-dlp(인터넷 영상 내려받기)는 포함한다. 추출기 모듈이 지연 import되므로 collect-submodules가 필요하다.
# curl_cffi(TikTok·Instagram용 브라우저 위장)의 libcurl-impersonate DLL은 패키지 옆 curl_cffi.libs 폴더에서 찾으므로 같은 위치로 넣는다.
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
    $CurlLibs = & $Py -c "import curl_cffi, os; print(os.path.join(os.path.dirname(os.path.dirname(curl_cffi.__file__)), 'curl_cffi.libs'))"
    if (-not (Test-Path $CurlLibs)) { throw "curl_cffi.libs 폴더를 찾지 못했습니다: $CurlLibs" }
    & $Py -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name vcam `
        --icon "$Root\assets\icons\vcam.ico" `
        --add-data "$Root\assets;assets" `
        --add-data "$Root\THIRD_PARTY_NOTICES.md;." `
        --add-data "$Root\LICENSE;." `
        --collect-submodules dxcam `
        --collect-data faster_whisper `
        --collect-binaries ctranslate2 `
        --collect-submodules yt_dlp `
        --collect-data yt_dlp_ejs `
        --collect-submodules yt_dlp_ejs `
        --collect-binaries curl_cffi `
        --add-binary "$CurlLibs;curl_cffi.libs" `
        --exclude-module av `
        --runtime-hook "$Root\scripts\pyi_rth_no_pyav.py" `
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
