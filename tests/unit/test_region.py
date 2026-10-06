from PySide6.QtCore import QPointF, QRectF

from vcam.capture.region import MIN_REGION, aspect_rect, clamp_rect, preset_rect
from vcam.domain.models import Rect
from vcam.ui.widgets.region_overlay import clamp_into, hit_test, resize_rect, snap_to

SCREEN = Rect(-1920, 0, 1920, 1080)  # 주 모니터 왼쪽에 있는 음수 좌표 모니터


def test_preset_rect_centered_on_negative_monitor():
    r = preset_rect(SCREEN, 1280, 720)
    assert (r.width, r.height) == (1280, 720)
    assert SCREEN.contains_rect(r)
    assert r.left == -1920 + (1920 - 1280) // 2


def test_preset_rect_shrinks_to_fit():
    r = preset_rect(Rect(0, 0, 1280, 720), 1920, 1080)
    assert (r.width, r.height) == (1280, 720)


def test_aspect_rect():
    r = aspect_rect(Rect(100, 100, 800, 800), Rect(0, 0, 1920, 1080), 16, 9)
    assert (r.width, r.height) == (800, 450)


def test_clamp_rect():
    assert clamp_rect(Rect(1900, 1000, 100, 100), Rect(0, 0, 1920, 1080)) == Rect(1820, 980, 100, 100)
    assert clamp_rect(Rect(0, 0, 1, 1), Rect(0, 0, 1920, 1080)).width == MIN_REGION


def test_rect_intersection():
    assert Rect(0, 0, 10, 10).intersected(Rect(5, 5, 10, 10)) == Rect(5, 5, 5, 5)
    assert Rect(0, 0, 10, 10).intersected(Rect(20, 20, 5, 5)).is_empty


def test_overlay_hit_and_resize():
    r = QRectF(100, 100, 200, 100)
    assert hit_test(r, QPointF(100, 100)) == "nw"
    assert hit_test(r, QPointF(300, 150)) == "e"
    assert hit_test(r, QPointF(200, 150)) == "move"
    assert hit_test(r, QPointF(10, 10)) is None
    grown = resize_rect(r, "se", QPointF(50, 20))
    assert (grown.width(), grown.height()) == (250, 120)
    assert resize_rect(r, "w", QPointF(1000, 0)).width() >= MIN_REGION / 2


def test_overlay_clamp_and_snap():
    bounds = QRectF(0, 0, 1920, 1080)
    assert clamp_into(QRectF(1800, 1000, 200, 100), bounds, keep_size=True) == QRectF(1720, 980, 200, 100)
    snapped = snap_to(QRectF(5, 3, 500, 500), bounds)
    assert (snapped.left(), snapped.top()) == (0, 0)
