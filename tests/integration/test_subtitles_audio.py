"""자막 파이프라인의 FFmpeg 소리 추출을 실제 FFmpeg로 검증한다."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from vcam.subtitles import transcriber as tr

pytestmark = pytest.mark.integration


def make_media(ffmpeg, path, seconds=2.0, audio=True):
    args = [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}"]  # fmt: skip
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-shortest", str(path)]
    subprocess.run(args, check=True)
    return path


def test_decode_audio_is_16k_mono_float32(ffmpeg, tmp_path):
    video = make_media(ffmpeg, tmp_path / "tone.mp4", seconds=2.0)
    audio = tr.decode_audio(ffmpeg, video)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert abs(audio.size - 2.0 * tr.SAMPLE_RATE) < tr.SAMPLE_RATE * 0.2  # AAC 프라이밍 오차 허용
    assert np.abs(audio).max() > 0.1  # 실제 신호가 들어 있다


def test_decode_audio_without_track_raises(ffmpeg, tmp_path):
    video = make_media(ffmpeg, tmp_path / "silent.mp4", seconds=1.0, audio=False)
    with pytest.raises(tr.SubtitleError):
        tr.decode_audio(ffmpeg, video)


def test_decode_audio_cancel(ffmpeg, tmp_path):
    video = make_media(ffmpeg, tmp_path / "tone.mp4", seconds=2.0)
    with pytest.raises(tr.Cancelled):
        tr.decode_audio(ffmpeg, video, cancel=lambda: True)
