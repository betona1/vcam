"""녹화 컨트롤 바가 캡처에서 제외되는지 확인한다 (Windows 10 2004+)."""

import ctypes
import sys

import pytest
from PySide6.QtCore import QRect

from vcam.ui.theme import apply_theme
from vcam.ui.widgets.recording_bar import RecordingBar

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 전용")

WS_EX_LAYERED = 0x00080000
WDA_EXCLUDEFROMCAPTURE = 0x11


def test_bar_is_not_layered_and_excluded_from_capture(qtbot, qapp):
    bar = RecordingBar(apply_theme(qapp, "light"))
    qtbot.addWidget(bar)
    bar.show_near(QRect(100, 100, 400, 300))
    qtbot.waitExposed(bar)
    hwnd = ctypes.c_void_p(int(bar.winId()))
    user32 = ctypes.windll.user32
    exstyle = user32.GetWindowLongW(hwnd, -20)
    assert not (exstyle & WS_EX_LAYERED), "반투명(레이어드) 창은 캡처 제외가 무시된다"
    affinity = ctypes.c_uint(0)
    assert user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
    assert affinity.value == WDA_EXCLUDEFROMCAPTURE
    assert not bar.mask().isEmpty()  # 둥근 모양은 마스크가 만든다


def test_bar_warning_toggle(qtbot, qapp):
    bar = RecordingBar(apply_theme(qapp, "dark"))
    qtbot.addWidget(bar)
    bar.show()
    bar.set_warning("검은 화면", "설명")
    assert not bar.warn_label.isHidden() and "검은 화면" in bar.warn_label.text()
    bar.set_warning(None)
    assert bar.warn_label.isHidden()
