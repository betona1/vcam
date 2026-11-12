"""녹화 중 화면이 그대로인지 판단하는 샘플 비교."""

import numpy as np

from vcam.services.recording_pipeline import screen_changed, still_sample


def frame(h=720, w=1280, value=40):
    f = np.full((h, w, 4), value, dtype=np.uint8)
    f[..., 3] = 255
    return f


def test_identical_frames_are_still():
    assert not screen_changed(still_sample(frame()), still_sample(frame()))


def test_alpha_and_tiny_noise_are_ignored():
    a, b = frame(), frame()
    b[..., 3] = 0
    b[..., :3] += 10  # 압축·디더 수준의 작은 차이
    assert not screen_changed(still_sample(a), still_sample(b))


def test_blinking_caret_or_clock_digits_do_not_count():
    a, b = frame(), frame()
    b[700:716, 1200:1240, :3] = 255  # 작업 표시줄 시계 숫자 정도의 변화
    b[100:120, 300:302, :3] = 255  # 깜빡이는 텍스트 커서
    assert not screen_changed(still_sample(a), still_sample(b))


def test_window_sized_change_counts():
    a, b = frame(), frame()
    b[200:320, 400:600, :3] = 255
    assert screen_changed(still_sample(a), still_sample(b))


def test_size_change_counts_and_sample_does_not_alias_frame():
    a = frame()
    s = still_sample(a)
    a[...] = 255
    assert int(s.max()) == 40, "샘플은 캡처 버퍼가 재사용돼도 바뀌면 안 된다"
    assert screen_changed(s, still_sample(frame(360, 640)))
