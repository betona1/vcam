"""FFmpeg/ffprobe 위치 찾기와 실행 도우미."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from vcam.util.paths import app_data_dir

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass(frozen=True)
class FfmpegPaths:
    ffmpeg: Path
    ffprobe: Path


def _pair_in(directory: Path) -> FfmpegPaths | None:
    ffmpeg, ffprobe = directory / "ffmpeg.exe", directory / "ffprobe.exe"
    if ffmpeg.is_file() and ffprobe.is_file():
        return FfmpegPaths(ffmpeg, ffprobe)
    return None


def find_ffmpeg(custom: str | None = None) -> FfmpegPaths | None:
    """사용자 지정 경로 → 앱 데이터 → 실행 파일 옆 → PATH → winget 링크 순으로 찾는다."""
    candidates: list[Path] = []
    if custom:
        p = Path(custom)
        candidates.append(p.parent if p.is_file() else p)
        candidates.append((p.parent if p.is_file() else p) / "bin")
    candidates.append(app_data_dir() / "ffmpeg" / "bin")
    candidates.append(Path(sys.executable).parent / "ffmpeg")
    for directory in candidates:
        found = _pair_in(directory)
        if found:
            return found
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return FfmpegPaths(Path(ffmpeg), Path(ffprobe))
    # winget 설치 직후에는 PATH가 아직 갱신되지 않았을 수 있다.
    for base in (Path(os.environ.get("LOCALAPPDATA", "")), Path.home() / "AppData" / "Local"):
        found = _pair_in(base / "Microsoft" / "WinGet" / "Links")
        if found:
            return found
    return None


def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
    )


def ffmpeg_version(paths: FfmpegPaths) -> str:
    try:
        out = run([str(paths.ffmpeg), "-hide_banner", "-version"], timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"실행 실패: {exc}"
    first = out.splitlines()[0] if out else ""
    return first.replace("ffmpeg version ", "").split(" Copyright")[0]
