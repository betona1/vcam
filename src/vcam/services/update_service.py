"""GitHub 릴리스 기반 자동 업데이트(Qt 비의존).

흐름: 최신 릴리스 조회 → 새 버전이면 zip과 .sha256을 내려받아 해시 검증 → 임시 폴더에 안전하게 압축 해제
→ 앱이 끝나면 별도 PowerShell 스크립트가 설치 폴더 파일을 교체(실패 시 원복)하고 다시 실행한다.

보내는 정보는 GitHub API 요청과 User-Agent의 vcam 버전뿐이다. 화면·소리·파일명은 보내지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vcam import GITHUB_REPO, __version__
from vcam.util.paths import app_data_dir, logs_dir

log = logging.getLogger(__name__)

ASSET_PATTERN = re.compile(r"^vcam-.+-win64\.zip$")
EXE_NAME = "vcam.exe"
CHUNK = 256 * 1024

Fetcher = Callable[[str, float], bytes]


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    html_url: str
    zip_url: str
    zip_name: str
    sha256_url: str
    size: int


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' / '1.2.3-beta' → (1, 2, 3). 숫자가 아닌 꼬리는 무시한다."""
    core = text.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for piece in core.split("."):
        digits = re.match(r"\d+", piece)
        if not digits:
            break
        parts.append(int(digits.group()))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def _http_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"vcam/{__version__}", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https 고정 URL
        return response.read()


def parse_release(data: dict, repo: str = GITHUB_REPO) -> ReleaseInfo | None:
    if data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name", ""): a for a in data.get("assets", [])}
    zip_asset = next((a for name, a in assets.items() if ASSET_PATTERN.match(name)), None)
    if zip_asset is None:
        return None
    sha_asset = assets.get(zip_asset["name"] + ".sha256")
    if sha_asset is None:
        log.warning("릴리스 %s에 체크섬 파일이 없어 자동 설치하지 않습니다", data.get("tag_name"))
        return None
    prefix = f"https://github.com/{repo}/releases/download/"
    for asset in (zip_asset, sha_asset):
        if not str(asset.get("browser_download_url", "")).startswith(prefix):
            raise UpdateError("예상하지 못한 다운로드 주소라서 업데이트를 중단했습니다")
    tag = str(data.get("tag_name", ""))
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or ""),
        html_url=str(data.get("html_url") or f"https://github.com/{repo}/releases"),
        zip_url=zip_asset["browser_download_url"],
        zip_name=zip_asset["name"],
        sha256_url=sha_asset["browser_download_url"],
        size=int(zip_asset.get("size") or 0),
    )


def fetch_latest(repo: str = GITHUB_REPO, fetch: Fetcher = _http_get, timeout: float = 10) -> ReleaseInfo | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        data = json.loads(fetch(url, timeout))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # 아직 릴리스가 없음
        raise UpdateError(f"업데이트 정보를 받지 못했습니다 (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise UpdateError(f"업데이트 서버에 연결하지 못했습니다: {exc}") from exc
    return parse_release(data, repo)


def updates_dir() -> Path:
    path = app_data_dir() / "updates"
    path.mkdir(exist_ok=True)
    return path


def download_release(
    release: ReleaseInfo,
    dest: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> Path:
    """zip을 내려받고 릴리스의 .sha256과 비교한다. 해시가 다르면 파일을 지우고 실패한다."""
    dest = dest or updates_dir()
    target = dest / release.zip_name
    part = target.with_suffix(".part")
    headers = {"User-Agent": f"vcam/{__version__}"}
    try:
        with opener(urllib.request.Request(release.sha256_url, headers=headers), timeout=30) as r:
            expected = r.read().decode("ascii", "replace").split()[0].strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError("체크섬 파일 형식이 올바르지 않습니다")
        digest = hashlib.sha256()
        done = 0
        with opener(urllib.request.Request(release.zip_url, headers=headers), timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(CHUNK):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, release.size)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"업데이트를 내려받지 못했습니다: {exc}") from exc
    if digest.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("내려받은 파일의 체크섬이 맞지 않아 설치하지 않습니다")
    os.replace(part, target)
    log.info("업데이트 다운로드 완료: %s (%d bytes, sha256 확인)", release.zip_name, done)
    return target


def extract_release(zip_path: Path, staging: Path | None = None) -> Path:
    """zip을 안전하게 풀고(경로 탈출 차단) vcam.exe가 있는 폴더를 돌려준다."""
    staging = staging or updates_dir() / "staged"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    root = staging.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (staging / member.filename).resolve()
            if root not in target.parents and target != root:
                raise UpdateError(f"압축 파일에 잘못된 경로가 있습니다: {member.filename}")
        zf.extractall(staging)
    candidates = [p.parent for p in staging.rglob(EXE_NAME)]
    if len(candidates) != 1:
        raise UpdateError("업데이트 파일 안에서 vcam.exe를 찾지 못했습니다")
    return candidates[0]


def install_dir() -> Path | None:
    """PyInstaller로 만든 실행 파일일 때만 설치 폴더를 돌려준다(개발 실행은 자동 설치 안 함)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def can_write(directory: Path) -> bool:
    probe = directory / ".vcam-update-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


APPLY_SCRIPT = r"""
param([int]$ProcessId, [string]$Staged, [string]$Target, [string]$Log, [int]$Restart)
$ErrorActionPreference = "Stop"
function Write-Log($m) { Add-Content -Path $Log -Value ("{0:u} {1}" -f (Get-Date), $m) -Encoding UTF8 }
Write-Log "update start: pid=$ProcessId"
try { Wait-Process -Id $ProcessId -Timeout 120 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$backup = Join-Path $Target (".vcam-backup-" + $stamp)
New-Item -ItemType Directory -Path $backup | Out-Null
$items = Get-ChildItem -LiteralPath $Staged -Force
try {
    foreach ($item in $items) {
        $dest = Join-Path $Target $item.Name
        if (Test-Path -LiteralPath $dest) { Move-Item -LiteralPath $dest -Destination $backup -Force }
        Copy-Item -LiteralPath $item.FullName -Destination $dest -Recurse -Force
    }
    Write-Log "update applied"
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
} catch {
    Write-Log ("update failed, rolling back: " + $_)
    foreach ($item in $items) {
        $dest = Join-Path $Target $item.Name
        $saved = Join-Path $backup $item.Name
        if (Test-Path -LiteralPath $saved) {
            if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue }
            Move-Item -LiteralPath $saved -Destination $dest -Force
        }
    }
}
Remove-Item -LiteralPath $Staged -Recurse -Force -ErrorAction SilentlyContinue
if ($Restart -eq 1) { Start-Process -FilePath (Join-Path $Target "vcam.exe") }
"""


def write_apply_script() -> Path:
    path = updates_dir() / "apply_update.ps1"
    path.write_text(APPLY_SCRIPT.lstrip(), encoding="utf-8-sig")
    return path


# DETACHED_PROCESS(0x8)를 쓰면 powershell.exe가 콘솔 없이 즉시 종료되어 스크립트가 실행되지 않는다(v0.2.0 버그).
# 숨은 콘솔을 주는 CREATE_NO_WINDOW와 새 프로세스 그룹이면 앱이 끝난 뒤에도 독립적으로 계속 실행된다.
APPLY_CREATION_FLAGS = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def launch_apply(
    staged: Path, target: Path, restart: bool, *, wait_pid: int | None = None, log_path: Path | None = None
) -> subprocess.Popen:
    """현재 프로세스가 끝나기를 기다렸다가 파일을 교체하는 스크립트를 독립 실행한다."""
    if not (staged / EXE_NAME).exists():
        raise UpdateError("준비된 업데이트 파일이 없습니다")
    if not can_write(target):
        raise UpdateError(f"설치 폴더에 쓸 수 없습니다: {target}")
    script = write_apply_script()
    proc = subprocess.Popen(  # noqa: S603
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-File", str(script), "-ProcessId", str(wait_pid or os.getpid()), "-Staged", str(staged),
         "-Target", str(target), "-Log", str(log_path or logs_dir() / "update.log"),
         "-Restart", "1" if restart else "0"],
        creationflags=APPLY_CREATION_FLAGS, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    log.info("업데이트 적용 스크립트 실행 (재시작=%s)", restart)
    return proc
