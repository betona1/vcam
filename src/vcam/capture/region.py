"""모니터 목록과 영역 계산."""

from __future__ import annotations

from vcam.capture.mss_compat import mss_factory
from vcam.domain.models import MonitorInfo, Rect

# (표시 이름, 너비, 높이) — 너비/높이가 0이면 비율 프리셋
SIZE_PRESETS: list[tuple[str, int, int]] = [
    ("1280 × 720 (720p)", 1280, 720),
    ("1920 × 1080 (1080p)", 1920, 1080),
    ("854 × 480", 854, 480),
    ("1080 × 1080 (정사각형)", 1080, 1080),
]
ASPECT_PRESETS: list[tuple[str, int, int]] = [("16:9", 16, 9), ("4:3", 4, 3), ("1:1", 1, 1)]

MIN_REGION = 32


def list_monitors() -> list[MonitorInfo]:
    with mss_factory() as sct:
        monitors = sct.monitors[1:]
    result = []
    for i, m in enumerate(monitors, start=1):
        rect = Rect(m["left"], m["top"], m["width"], m["height"])
        primary = m.get("is_primary", rect.left == 0 and rect.top == 0)
        result.append(MonitorInfo(index=i, rect=rect, is_primary=bool(primary)))
    return result


def virtual_screen() -> Rect:
    with mss_factory() as sct:
        m = sct.monitors[0]
    return Rect(m["left"], m["top"], m["width"], m["height"])


def preset_rect(bounds: Rect, width: int, height: int, center: tuple[int, int] | None = None) -> Rect:
    """bounds 안에 들어가는 지정 크기 사각형. 공간이 모자라면 비율을 유지해 줄인다."""
    scale = min(1.0, bounds.width / width, bounds.height / height)
    w, h = max(MIN_REGION, int(width * scale)), max(MIN_REGION, int(height * scale))
    cx, cy = center or (bounds.left + bounds.width // 2, bounds.top + bounds.height // 2)
    left = min(max(bounds.left, cx - w // 2), bounds.right - w)
    top = min(max(bounds.top, cy - h // 2), bounds.bottom - h)
    return Rect(left, top, w, h)


def aspect_rect(current: Rect, bounds: Rect, aw: int, ah: int) -> Rect:
    """현재 영역 중심을 유지하며 지정 비율로 맞춘다."""
    w = current.width
    h = round(w * ah / aw)
    if h > bounds.height:
        h = bounds.height
        w = round(h * aw / ah)
    return preset_rect(bounds, w, h, (current.left + current.width // 2, current.top + current.height // 2))


def clamp_rect(rect: Rect, bounds: Rect) -> Rect:
    w = min(max(MIN_REGION, rect.width), bounds.width)
    h = min(max(MIN_REGION, rect.height), bounds.height)
    left = min(max(bounds.left, rect.left), bounds.right - w)
    top = min(max(bounds.top, rect.top), bounds.bottom - h)
    return Rect(left, top, w, h)
