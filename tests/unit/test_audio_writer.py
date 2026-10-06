import numpy as np
import pytest

from vcam.audio.meter import SILENCE_DB, LevelMeter, block_levels
from vcam.audio.track_writer import AlignedPcmWriter, AudioTrack, to_s16le
from vcam.util.clock import SessionClock

SR = 48_000


class FakeNow:
    def __init__(self):
        self.t = 0

    def __call__(self):
        return self.t


def ms(n):
    return n * 1_000_000


@pytest.fixture
def setup(tmp_path):
    now = FakeNow()
    clock = SessionClock(now)
    track = AudioTrack("system", tmp_path / "system.pcm", SR, 2)
    return now, clock, AlignedPcmWriter(track, clock)


def block(frames, value=0.5):
    return np.full((frames, 2), value, dtype=np.float32)


def test_ignores_audio_before_clock_start(setup):
    now, clock, w = setup
    w.write(block(960))
    assert w.samples_written == 0


def test_steady_stream_is_written_unchanged(setup):
    now, clock, w = setup
    clock.start()
    for _ in range(50):
        now.t += ms(20)
        w.write(block(960))
    assert w.samples_written == 50 * 960
    stats = w.finish(1.0)
    assert stats.padded_samples == 0 and stats.trimmed_samples == 0


def test_gap_is_filled_with_silence(setup):
    now, clock, w = setup
    clock.start()
    now.t += ms(20)
    w.write(block(960))
    now.t += ms(500)  # 장치가 0.5초 멈춤
    w.write(block(960))
    assert abs(w.samples_written - round(0.52 * SR)) <= 1


def test_fast_device_clock_is_trimmed(setup):
    now, clock, w = setup
    clock.start()
    for _ in range(100):  # 장치가 1% 빠르다: 2초 동안 2.02초 분량이 도착
        now.t += ms(20)
        w.write(block(970))
    expected = 2 * SR
    assert abs(w.samples_written - expected) <= 0.04 * SR + 970


def test_paused_audio_is_discarded(setup):
    now, clock, w = setup
    clock.start()
    now.t += ms(20)
    w.write(block(960))
    clock.pause()
    for _ in range(10):
        now.t += ms(20)
        w.write(block(960))
    clock.resume()
    now.t += ms(20)
    w.write(block(960))
    assert w.samples_written == 2 * 960


def test_finish_matches_video_length_exactly(setup, tmp_path):
    now, clock, w = setup
    clock.start()
    now.t += ms(20)
    w.write(block(960))
    stats = w.finish(1.5)
    assert stats.samples_written == round(1.5 * SR)
    assert (tmp_path / "system.pcm").stat().st_size == round(1.5 * SR) * 2 * 2
    assert stats.padded_samples > 0


def test_finish_truncates_when_audio_longer(setup, tmp_path):
    now, clock, w = setup
    clock.start()
    now.t += ms(1000)
    w.write(block(SR))
    stats = w.finish(0.5)
    assert (tmp_path / "system.pcm").stat().st_size == SR // 2 * 4
    assert abs(stats.end_offset_ms - 500) < 1


def test_s16_conversion_clips():
    data = np.frombuffer(to_s16le(np.array([[2.0, -2.0]], dtype=np.float32)), dtype="<i2")
    assert list(data) == [32767, -32767]


def test_levels():
    assert block_levels(np.zeros((10, 1), dtype=np.float32)) == (SILENCE_DB, SILENCE_DB)
    peak, rms = block_levels(np.full((10, 1), 0.5, dtype=np.float32))
    assert abs(peak - (-6.02)) < 0.05 and abs(rms - (-6.02)) < 0.05


def test_meter_peak_hold_decays():
    m = LevelMeter()
    m.update(np.full((10, 1), 1.0, dtype=np.float32))
    m.update(np.zeros((10, 1), dtype=np.float32))
    assert -2.0 < m.read().peak_db < 0.0
    assert m.read().rms_db == SILENCE_DB
