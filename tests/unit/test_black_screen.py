"""검은 화면 감지: 보호된 콘텐츠(DRM)처럼 캡처가 검게 나오는 상황을 사용자에게 알린다."""

from pathlib import Path

import numpy as np

from vcam.domain.models import Rect
from vcam.services.recording_pipeline import (
    BLACK_CONSECUTIVE,
    BLACK_SCREEN_NOTE,
    PipelineConfig,
    RecordingPipeline,
    looks_black,
)


def test_looks_black_ignores_alpha_and_samples():
    bgra = np.zeros((200, 300, 4), dtype=np.uint8)
    bgra[:, :, 3] = 255  # 불투명 알파만 있는 완전 검정
    assert looks_black(bgra)
    bgra[64, 96, 1] = 200  # 32px 격자 위의 밝은 픽셀
    assert not looks_black(bgra)
    assert looks_black(np.full((100, 100, 3), 10, dtype=np.uint8))
    assert not looks_black(np.full((100, 100, 3), 40, dtype=np.uint8))


def make_pipeline(tmp_path: Path) -> RecordingPipeline:
    config = PipelineConfig(
        rect=Rect(0, 0, 64, 64), fps=30, encoder_key="x264", quality="balanced", ffmpeg=None,
        partial_path=tmp_path / "a.partial.mkv", ffmpeg_log=tmp_path / "ffmpeg.log",
    )  # fmt: skip
    return RecordingPipeline(config, open_backend=lambda _r: None, on_fatal=lambda _e: None)


def test_black_streak_sets_metric_and_single_note(tmp_path):
    p = make_pipeline(tmp_path)
    for _ in range(BLACK_CONSECUTIVE - 1):
        p._note_black(True)
    assert p.metrics().black_screen is False
    p._note_black(True)
    assert p.metrics().black_screen is True
    p._note_black(False)  # 다시 밝아지면 표시는 꺼지지만
    assert p.metrics().black_screen is False
    for _ in range(BLACK_CONSECUTIVE + 2):
        p._note_black(True)
    assert p._notes.count(BLACK_SCREEN_NOTE) == 1  # 결과 메모는 한 번만
