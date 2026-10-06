"""중간 MKV(+ 원시 PCM 오디오)를 최종 MP4로 합치고, 검증 후 원자적으로 최종 이름을 붙인다."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from vcam.audio.track_writer import AudioTrack
from vcam.domain.models import MediaInfo
from vcam.encoding.ffmpeg import FfmpegPaths, run
from vcam.encoding.validator import ValidationError, validate_recording
from vcam.util.paths import redact_path, unique_path

log = logging.getLogger(__name__)

AUDIO_BITRATE = "192k"


class MuxError(Exception):
    pass


def build_mux_args(
    ffmpeg: FfmpegPaths, src: Path, dst: Path, audio: Sequence[AudioTrack] = (), tolerant: bool = False
) -> list[str]:
    args = [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    if tolerant:
        # 비정상 종료로 꼬리가 깨진 MKV도 읽을 수 있는 만큼 살린다.
        args += ["-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt"]
    args += ["-i", str(src)]
    tracks = [t for t in audio if t.path.exists() and t.path.stat().st_size > 0]
    if not tracks:
        return [*args, "-map", "0:v", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(dst)]
    for t in tracks:
        args += ["-f", "s16le", "-ar", str(t.samplerate), "-ac", str(t.channels), "-i", str(t.path)]
    inputs = "".join(f"[{i}:a]" for i in range(1, len(tracks) + 1))
    if len(tracks) == 1:
        graph = f"{inputs}aformat=channel_layouts=stereo,apad[a]"
    else:
        # 시스템 소리와 마이크를 그대로 더하고(normalize=0), 합이 넘치면 리미터로 클리핑을 막는다.
        graph = (f"{inputs}amix=inputs={len(tracks)}:duration=longest:normalize=0,"
                 "alimiter=limit=0.97:level=disabled,aformat=channel_layouts=stereo,apad[a]")  # fmt: skip
    return [
        *args, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000",
        "-shortest", "-movflags", "+faststart", "-f", "mp4", str(dst),
    ]  # fmt: skip


def remux_to_mp4(
    ffmpeg: FfmpegPaths, src: Path, dst: Path, tolerant: bool = False, audio: Sequence[AudioTrack] = ()
) -> None:
    args = build_mux_args(ffmpeg, src, dst, audio, tolerant)
    try:
        result = run(args, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MuxError(f"FFmpeg 실행 실패: {exc}") from exc
    if result.returncode != 0 or not dst.exists():
        raise MuxError(f"MP4로 변환하지 못했습니다: {result.stderr.strip()[:300]}")


def finalize_to_output(
    ffmpeg: FfmpegPaths,
    partial: Path,
    output_dir: Path,
    stem: str,
    tolerant: bool = False,
    audio: Sequence[AudioTrack] = (),
) -> MediaInfo:
    """partial MKV(+PCM) → 임시 MP4 → ffprobe 검증 → 충돌 없는 최종 이름으로 이동."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp = output_dir / f".{stem}.vcam-tmp.mp4"
    expect_audio = any(t.path.exists() and t.path.stat().st_size > 0 for t in audio)
    try:
        remux_to_mp4(ffmpeg, partial, tmp, tolerant=tolerant, audio=audio)
        info = validate_recording(ffmpeg, tmp, expect_audio=expect_audio)
        final = unique_path(output_dir, stem, ".mp4")
        os.replace(tmp, final)
    except (MuxError, ValidationError, OSError):
        tmp.unlink(missing_ok=True)
        raise
    info = replace(info, path=final)
    log.info("녹화 저장 완료: %s (%.1f초, 오디오 %s)", redact_path(final), info.duration_s, info.audio_codec or "없음")
    return info


def finalize_with_fallback(
    ffmpeg: FfmpegPaths,
    partial: Path,
    output_dir: Path,
    stem: str,
    audio: Sequence[AudioTrack] = (),
    tolerant: bool = False,
) -> tuple[MediaInfo, list[str]]:
    """소리를 합치다 실패해도 영상은 잃지 않는다: 오디오 포함 → 영상만 순으로 시도한다."""
    notes: list[str] = []
    if audio:
        try:
            return finalize_to_output(ffmpeg, partial, output_dir, stem, tolerant, audio), notes
        except (MuxError, ValidationError) as exc:
            log.error("오디오 합치기 실패, 영상만 저장합니다: %s", exc)
            notes.append("소리를 합치지 못해 영상만 저장했습니다.")
    return finalize_to_output(ffmpeg, partial, output_dir, stem, tolerant), notes
