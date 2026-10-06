"""비정상 종료로 남은 미완료 녹화 세션을 찾아 복구한다."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vcam.audio.track_writer import AudioTrack
from vcam.domain.models import MediaInfo, SessionPaths
from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.encoding.muxer import finalize_with_fallback
from vcam.platform.windows.trash import send_to_trash
from vcam.util.paths import sessions_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncompleteSession:
    paths: SessionPaths
    meta: dict
    size_bytes: int

    @property
    def started_label(self) -> str:
        started = self.meta.get("started_at")
        try:
            return datetime.fromisoformat(started).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return self.paths.directory.name


def write_session_meta(paths: SessionPaths, meta: dict) -> None:
    tmp = paths.meta.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(paths.meta)


def find_incomplete_sessions(root: Path | None = None, exclude: Path | None = None) -> list[IncompleteSession]:
    root = root or sessions_dir()
    found: list[IncompleteSession] = []
    for directory in sorted(root.iterdir()) if root.exists() else []:
        if not directory.is_dir() or (exclude is not None and directory == exclude):
            continue
        paths = SessionPaths.in_dir(directory)
        if not paths.video_partial.exists():
            # 영상이 없는 빈 세션 디렉터리는 정리한다.
            shutil.rmtree(directory, ignore_errors=True)
            continue
        size = paths.video_partial.stat().st_size
        if size == 0:
            shutil.rmtree(directory, ignore_errors=True)
            continue
        try:
            meta = json.loads(paths.meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        found.append(IncompleteSession(paths, meta, size))
    return found


def recover_session(
    session: IncompleteSession, ffmpeg: FfmpegPaths, output_dir: Path, fallback_stem: str
) -> MediaInfo:
    stem = str(session.meta.get("output_stem") or fallback_stem) + "_복구"
    tracks = []
    for meta in session.meta.get("audio", []):
        try:
            tracks.append(AudioTrack.from_meta(session.paths.directory, meta))
        except (KeyError, ValueError, TypeError):
            log.warning("세션 메타데이터의 오디오 트랙 정보가 올바르지 않습니다: %s", meta)
    info, _notes = finalize_with_fallback(ffmpeg, session.paths.video_partial, output_dir, stem, tracks, tolerant=True)
    shutil.rmtree(session.paths.directory, ignore_errors=True)
    return info


def discard_session(session: IncompleteSession) -> None:
    send_to_trash(session.paths.directory)
