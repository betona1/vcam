"""녹화 바는 평소 작은 아이콘으로 접혀 있고, 마우스를 올리면 버튼이 펼쳐진다."""

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect
from PySide6.QtGui import QEnterEvent

from vcam.ui.theme import apply_theme
from vcam.ui.widgets import recording_bar as rb
from vcam.ui.widgets.recording_bar import COMPACT_SIZE, RecordingBar


@pytest.fixture
def bar(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(rb, "INTRO_MS", 50)
    monkeypatch.setattr(rb, "COLLAPSE_DELAY_MS", 50)
    b = RecordingBar(apply_theme(qapp, "light"))
    qtbot.addWidget(b)
    b.show_near(QRect(100, 100, 400, 300))
    qtbot.waitExposed(b)
    return b


def _enter(bar):
    bar.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))


def test_starts_expanded_then_collapses_to_icon(qtbot, bar):
    assert not bar.compact and bar.stop_btn.isVisible(), "시작 직후에는 펼쳐 위치를 보여 준다"
    qtbot.waitUntil(lambda: bar.compact, timeout=2000)
    assert (bar.width(), bar.height()) == (COMPACT_SIZE, COMPACT_SIZE)
    assert not bar.stop_btn.isVisible() and not bar.time_label.isVisible()
    assert bar.state_label.text() == "●"


def test_hover_expands_and_leave_collapses(qtbot, bar):
    qtbot.waitUntil(lambda: bar.compact, timeout=2000)
    _enter(bar)
    assert not bar.compact
    assert bar.pause_btn.isVisible() and bar.stop_btn.isVisible() and bar.time_label.isVisible()
    assert bar.width() > COMPACT_SIZE
    bar.leaveEvent(QEvent(QEvent.Type.Leave))
    qtbot.waitUntil(lambda: bar.compact, timeout=2000)


def test_icon_shows_pause_and_warning(qtbot, bar):
    qtbot.waitUntil(lambda: bar.compact, timeout=2000)
    bar.set_paused(True)
    assert bar.state_label.text() == "❚❚"
    bar.set_warning("시스템 소리 녹음 안 됨", "장치 연결 끊김")
    assert bar.state_label.text() == "⚠" and "시스템 소리 녹음 안 됨" in bar.toolTip()
    assert not bar.warn_label.isVisible(), "접힌 상태에서는 아이콘만 바뀐다"
    bar.reveal(5000)
    assert not bar.compact and bar.warn_label.isVisible(), "중요한 경고는 잠시 펼쳐 보여 준다"
    bar.set_warning(None)
    assert not bar.warn_label.isVisible()
