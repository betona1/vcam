"""Qt 논리 좌표 ↔ 물리 픽셀 변환.

Qt는 프로세스를 Per-Monitor DPI Aware v2로 설정하므로 Win32 모니터 사각형은 물리 픽셀이다.
Qt 6는 각 화면의 좌상단 좌표를 물리 픽셀 그대로 두고 크기만 배율로 나누므로, 좌상단으로 두 좌표계를 잇는다.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QGuiApplication, QScreen

from vcam.domain.models import Rect


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)
# 전역 windll.user32의 argtypes를 바꾸지 않도록 별도 핸들을 쓴다. 64비트 핸들 잘림을 막으려면 argtypes가 필요하다.
_user32 = ctypes.WinDLL("user32")
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), _MONITORENUMPROC, wintypes.LPARAM]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL


def monitor_physical_rects() -> dict[str, Rect]:
    user32 = _user32
    result: dict[str, Rect] = {}
    proc_type = _MONITORENUMPROC

    def callback(hmon, _hdc, _rect, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            result[info.szDevice] = Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
        return True

    user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    return result


class ScreenMapper:
    """QScreen별 논리 사각형과 물리 사각형의 대응."""

    def __init__(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        physical = monitor_physical_rects()
        self._pairs: list[tuple[QScreen, QRect, Rect]] = []
        by_origin = {(r.left, r.top): r for r in physical.values()}
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            # Qt 6는 화면 좌상단을 물리 픽셀 그대로 유지한다. 이름은 Qt 버전에 따라 장치명/표시명이 섞인다.
            phys = by_origin.get((geo.x(), geo.y())) or physical.get(screen.name())
            if phys is None:  # 매칭 실패 시 배율로 근사
                dpr = screen.devicePixelRatio()
                phys = Rect(geo.x(), geo.y(), round(geo.width() * dpr), round(geo.height() * dpr))
            self._pairs.append((screen, geo, phys))

    def physical_rect(self, screen: QScreen) -> Rect:
        for s, _geo, phys in self._pairs:
            if s is screen:
                return phys
        raise KeyError(screen.name())

    def screen_for_physical(self, rect: Rect) -> QScreen:
        cx, cy = rect.left + rect.width // 2, rect.top + rect.height // 2
        for s, _geo, phys in self._pairs:
            if phys.left <= cx < phys.right and phys.top <= cy < phys.bottom:
                return s
        return QGuiApplication.primaryScreen()

    def _pair(self, screen: QScreen) -> tuple[QRect, Rect]:
        for s, geo, phys in self._pairs:
            if s is screen:
                return geo, phys
        raise KeyError(screen.name())

    def to_physical(self, screen: QScreen, logical: QPointF | QPoint) -> tuple[int, int]:
        geo, phys = self._pair(screen)
        sx, sy = phys.width / geo.width(), phys.height / geo.height()
        return (
            phys.left + round((logical.x() - geo.x()) * sx),
            phys.top + round((logical.y() - geo.y()) * sy),
        )

    def to_logical(self, screen: QScreen, x: int, y: int) -> QPointF:
        geo, phys = self._pair(screen)
        sx, sy = geo.width() / phys.width, geo.height() / phys.height
        return QPointF(geo.x() + (x - phys.left) * sx, geo.y() + (y - phys.top) * sy)

    def rect_to_logical(self, rect: Rect) -> tuple[QScreen, QRect]:
        screen = self.screen_for_physical(rect)
        tl = self.to_logical(screen, rect.left, rect.top)
        br = self.to_logical(screen, rect.right, rect.bottom)
        return screen, QRect(QPoint(round(tl.x()), round(tl.y())), QPoint(round(br.x()) - 1, round(br.y()) - 1))

    def scale(self, screen: QScreen) -> float:
        geo, phys = self._pair(screen)
        return phys.width / geo.width()
