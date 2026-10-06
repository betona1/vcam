"""저장 폴더의 최근 녹화 목록과 썸네일."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from vcam.domain.models import MediaInfo
from vcam.encoding.ffmpeg import FfmpegPaths, run
from vcam.encoding.validator import ValidationError, probe_media
from vcam.util.paths import cache_dir

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov"}


@dataclass(frozen=True)
class LibraryItem:
    path: Path
    media: MediaInfo | None
    thumbnail: Path | None
    modified: float


def list_recordings(directory: Path, limit: int = 30) -> list[Path]:
    if not directory.is_dir():
        return []
    files = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not p.name.startswith(".")
    ]  # fmt: skip
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def thumbnail_for(ffmpeg: FfmpegPaths, path: Path, media: MediaInfo | None) -> Path | None:
    stat = path.stat()
    key = hashlib.sha1(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:20]
    thumb = cache_dir() / f"thumb_{key}.jpg"
    if thumb.exists():
        return thumb
    at = min(1.0, (media.duration_s / 2) if media else 0.0)
    try:
        result = run(
            [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}",
             "-i", str(path), "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", str(thumb)],
            timeout=20,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    return thumb if result.returncode == 0 and thumb.exists() else None


def load_item(ffmpeg: FfmpegPaths | None, path: Path) -> LibraryItem:
    media = thumb = None
    if ffmpeg is not None:
        try:
            media = probe_media(ffmpeg, path)
        except ValidationError as exc:
            log.info("미디어 정보를 읽지 못함: %s", exc)
        thumb = thumbnail_for(ffmpeg, path, media)
    return LibraryItem(path, media, thumb, path.stat().st_mtime)
