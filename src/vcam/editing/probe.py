"""편집할 파일 분석(ffprobe): 길이, 영상·소리 형식, 키프레임 위치."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from vcam.encoding.ffmpeg import FfmpegPaths, run


class ProbeError(Exception):
    pass


@dataclass(frozen=True)
class MediaFile:
    path: Path
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str = ""
    pix_fmt: str = ""
    acodec: str = ""
    sample_rate: int = 0
    channels: int = 0
    start_time: float = 0.0  # TS·MPG 등은 타임스탬프가 0이 아닌 값에서 시작한다

    def copy_compatible(self, other: MediaFile) -> bool:
        """스트림 복사로 이어 붙일 수 있는지(코덱·해상도·화소 형식·소리 형식이 같아야 한다)."""
        return (
            self.has_video == other.has_video
            and self.has_audio == other.has_audio
            and (self.vcodec, self.width, self.height, self.pix_fmt) == (other.vcodec, other.width, other.height, other.pix_fmt)
            and abs(self.fps - other.fps) < 0.01
            and (self.acodec, self.sample_rate, self.channels) == (other.acodec, other.sample_rate, other.channels)
        )


def _fps(text: str) -> float:
    try:
        value = Fraction(text)
        return float(value) if value else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_file(ffmpeg: FfmpegPaths, path: Path) -> MediaFile:
    try:
        result = run(
            [str(ffmpeg.ffprobe), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"파일을 분석하지 못했습니다: {exc}") from exc
    if result.returncode != 0:
        raise ProbeError(f"동영상이나 소리 파일이 아니거나 손상되었습니다: {result.stderr.strip()[:200]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _float(data.get("format", {}).get("duration") or (video or audio or {}).get("duration"))
    if duration <= 0:
        raise ProbeError("재생 시간을 알 수 없는 파일입니다")
    return MediaFile(
        path=path,
        duration=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0") if video else 0.0,
        vcodec=str(video.get("codec_name", "")) if video else "",
        pix_fmt=str(video.get("pix_fmt", "")) if video else "",
        acodec=str(audio.get("codec_name", "")) if audio else "",
        sample_rate=int(audio.get("sample_rate", 0) or 0) if audio else 0,
        channels=int(audio.get("channels", 0) or 0) if audio else 0,
        start_time=_float(data.get("format", {}).get("start_time")),
    )


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def keyframes(ffmpeg: FfmpegPaths, path: Path, timeout: float = 120, start_time: float = 0.0) -> list[float]:
    """영상 키프레임 시각 목록(파일 시작 기준 초). 패킷 플래그만 읽으므로 디코딩보다 빠르다."""
    try:
        result = run(
            [str(ffmpeg.ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
            timeout=timeout,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    times = []
    for line in result.stdout.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                times.append(max(0.0, float(parts[0]) - start_time))
            except ValueError:
                continue
    return sorted(times)
