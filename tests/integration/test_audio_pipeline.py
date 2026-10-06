"""가짜 영상 + 가짜 오디오 장치 → 실제 FFmpeg 믹스 → ffprobe 검증."""

import time

import pytest

from vcam.audio.base import FakeAudioSource
from vcam.capture.base import FakeCaptureBackend
from vcam.domain.models import Rect, SessionPaths
from vcam.encoding.muxer import finalize_with_fallback
from vcam.services.audio_service import AudioConfig, AudioService
from vcam.services.recording_pipeline import PipelineConfig, RecordingPipeline

pytestmark = pytest.mark.integration


def make_service(**fake_kwargs):
    def factory(kind, _device):
        kw = fake_kwargs.get(kind, {})
        return FakeAudioSource(channels=2 if kind == "system" else 1, frequency=440 if kind == "system" else 880, **kw)

    return AudioService(factory)


class Session:
    def __init__(self, tmp_path, ffmpeg, service, config):
        self.paths = SessionPaths.in_dir(tmp_path / "session")
        self.paths.directory.mkdir()
        self.service = service
        service.configure(config)
        assert service.wait_ready(3) == []
        cfg = PipelineConfig(Rect(0, 0, 320, 240), 30, "x264", "balanced", ffmpeg,
                             self.paths.video_partial, self.paths.ffmpeg_log)  # fmt: skip

        def opener(r):
            b = FakeCaptureBackend()
            b.open(r)
            return b

        self.pipeline = RecordingPipeline(cfg, opener, lambda e: None)
        self.tracks = service.begin_recording(self.pipeline.clock, self.paths.directory)
        self.ffmpeg = ffmpeg
        self.tmp = tmp_path

    def run(self, seconds):
        self.pipeline.start()
        assert self.pipeline.wait_ready(5)
        time.sleep(seconds)

    def finish(self, stem="out"):
        outcome = self.pipeline.stop()
        stats = self.service.end_recording(outcome.frames_written / 30)
        info, notes = finalize_with_fallback(self.ffmpeg, self.paths.video_partial, self.tmp / "out", stem, self.tracks)
        self.service.shutdown()
        return outcome, stats, info, notes


def test_system_and_mic_are_mixed_and_aligned(tmp_path, ffmpeg):
    s = Session(tmp_path, ffmpeg, make_service(), AudioConfig(system_enabled=True, mic_enabled=True))
    assert [t.label for t in s.tracks] == ["system", "microphone"]
    s.run(3.0)
    _outcome, stats, info, notes = s.finish()
    assert notes == []
    assert info.audio_codec == "aac"
    assert abs(info.audio_duration_s - info.video_duration_s) < 0.1
    for st in stats:
        assert st.max_offset_ms < 100, st


def test_pause_removed_from_audio_too(tmp_path, ffmpeg):
    s = Session(tmp_path, ffmpeg, make_service(), AudioConfig(system_enabled=True))
    s.run(1.0)
    s.pipeline.pause()
    time.sleep(1.5)
    s.pipeline.resume()
    time.sleep(1.0)
    _o, stats, info, _n = s.finish()
    assert 1.8 <= info.duration_s <= 2.3
    assert abs(info.audio_duration_s - info.video_duration_s) < 0.1


def test_device_clock_drift_is_corrected(tmp_path, ffmpeg):
    """장치 시계가 2% 빠른 극단적인 경우에도 오디오가 영상 시간축을 벗어나지 않는다."""
    s = Session(tmp_path, ffmpeg, make_service(system={"rate_skew": 0.02}), AudioConfig(system_enabled=True))
    s.run(4.0)
    _o, stats, info, _n = s.finish()
    assert stats[0].trimmed_samples > 0
    assert stats[0].max_offset_ms < 100
    assert abs(stats[0].end_offset_ms) < 100
    assert abs(info.audio_duration_s - info.video_duration_s) < 0.1


def test_device_unplugged_mid_recording_keeps_video_and_timeline(tmp_path, ffmpeg):
    s = Session(tmp_path, ffmpeg, make_service(system={"fail_after_blocks": 50}), AudioConfig(system_enabled=True))
    s.run(3.0)
    _o, stats, info, notes = s.finish()
    assert notes == []
    assert info.audio_codec == "aac"
    assert stats[0].padded_samples > 0, "끊긴 구간은 무음으로 채워야 한다"
    assert abs(info.audio_duration_s - info.video_duration_s) < 0.1


def test_corrupt_audio_falls_back_to_video_only(tmp_path, ffmpeg):
    s = Session(tmp_path, ffmpeg, make_service(), AudioConfig(system_enabled=True))
    s.run(1.0)
    outcome = s.pipeline.stop()
    s.service.end_recording(outcome.frames_written / 30)
    s.service.shutdown()
    bad = [t.__class__(t.label, t.path, t.samplerate, 0) for t in s.tracks]  # 채널 0 → FFmpeg 거부
    info, notes = finalize_with_fallback(ffmpeg, s.paths.video_partial, tmp_path / "out", "fallback", bad)
    assert info.audio_codec == ""
    assert info.duration_s > 0.5
    assert notes and "영상만" in notes[0]


def test_mic_mute_records_silence(tmp_path, ffmpeg):
    s = Session(tmp_path, ffmpeg, make_service(), AudioConfig(system_enabled=False, mic_enabled=True))
    s.service.set_mic_muted(True)
    s.run(1.0)
    _o, _stats, info, _n = s.finish()
    assert info.audio_codec == "aac"
    import numpy as np

    pcm = np.frombuffer(s.tracks[0].path.read_bytes(), dtype="<i2")
    assert pcm.size > 0 and int(np.abs(pcm).max()) == 0
