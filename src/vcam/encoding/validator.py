"""ffprobe로 결과 파일을 검증한다."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vcam.domain.models import MediaInfo
from vcam.encoding.ffmpeg import FfmpegPaths, run


class ValidationError(Exception):
    pass


def probe_media(ffmpeg: FfmpegPaths, path: Path, timeout: float = 20) -> MediaInfo:
    try:
        result = run(
            [str(ffmpeg.ffprobe), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            timeout=timeout,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError(f"ffprobe 실행 실패: {exc}") from exc
    if result.returncode != 0:
        raise ValidationError(f"ffprobe가 파일을 읽지 못했습니다: {result.stderr.strip()[:300]}")
    data = json.loads(result.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValidationError("영상 스트림이 없습니다")
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    return MediaInfo(
        path=path,
        duration_s=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        codec=str(video.get("codec_name", "")),
        size_bytes=path.stat().st_size,
        audio_codec=str(audio.get("codec_name", "")) if audio else "",
        video_duration_s=float(video.get("duration") or 0.0),
        audio_duration_s=float(audio.get("duration") or 0.0) if audio else 0.0,
    )


def validate_recording(
    ffmpeg: FfmpegPaths, path: Path, min_duration_s: float = 0.2, expect_audio: bool = False
) -> MediaInfo:
    info = probe_media(ffmpeg, path)
    if expect_audio and not info.audio_codec:
        raise ValidationError("소리 스트림이 없습니다")
    if info.duration_s < min_duration_s:
        raise ValidationError(f"녹화 길이가 너무 짧습니다 ({info.duration_s:.2f}초)")
    if info.width <= 0 or info.height <= 0:
        raise ValidationError("영상 해상도를 확인할 수 없습니다")
    return info
