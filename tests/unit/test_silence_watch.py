"""녹화 중 시스템 소리 무음 감지."""

from vcam.audio.meter import DIGITAL_SILENCE_DB, SILENCE_DB, SilenceWatch


def test_silence_must_persist_for_hold_time():
    w = SilenceWatch(hold_s=5.0)
    assert w.update(SILENCE_DB, 0.0) is False
    assert w.update(SILENCE_DB, 4.9) is False
    assert w.update(SILENCE_DB, 5.0) is True
    assert w.update(SILENCE_DB, 60.0) is True


def test_any_real_sound_resets_the_clock():
    w = SilenceWatch(hold_s=5.0)
    w.update(SILENCE_DB, 0.0)
    assert w.update(-60.0, 3.0) is False  # 아주 작은 소리라도 무음이 아니다
    assert w.update(SILENCE_DB, 7.0) is False  # 소리가 끊긴 뒤 처음 본 무음(7.0)부터 다시 센다
    assert w.update(SILENCE_DB, 11.9) is False
    assert w.update(SILENCE_DB, 12.0) is True


def test_threshold_is_just_above_digital_silence():
    w = SilenceWatch(hold_s=1.0)
    assert w.update(DIGITAL_SILENCE_DB, 0.0) is False
    assert w.update(DIGITAL_SILENCE_DB, 1.0) is True
    assert w.update(DIGITAL_SILENCE_DB + 0.1, 2.0) is False


def test_reset_during_pause():
    w = SilenceWatch(hold_s=5.0)
    w.update(SILENCE_DB, 0.0)
    w.reset()
    assert w.update(SILENCE_DB, 4.0) is False
    assert w.update(SILENCE_DB, 9.0) is True
