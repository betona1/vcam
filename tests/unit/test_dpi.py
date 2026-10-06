from PySide6.QtCore import QPointF

from vcam.domain.models import Rect
from vcam.platform.windows.dpi import ScreenMapper, monitor_physical_rects


def test_every_qt_screen_maps_to_a_real_monitor(qapp):
    physical = set(monitor_physical_rects().values())
    mapper = ScreenMapper()
    for screen in qapp.screens():
        assert mapper.physical_rect(screen) in physical, screen.name()


def test_logical_physical_roundtrip(qapp):
    mapper = ScreenMapper()
    for screen in qapp.screens():
        geo = screen.geometry()
        pt = QPointF(geo.x() + geo.width() / 3, geo.y() + geo.height() / 4)
        x, y = mapper.to_physical(screen, pt)
        back = mapper.to_logical(screen, x, y)
        assert abs(back.x() - pt.x()) <= 1 and abs(back.y() - pt.y()) <= 1
        phys = mapper.physical_rect(screen)
        region = Rect(phys.left + 10, phys.top + 20, 300, 200)
        found_screen, logical = mapper.rect_to_logical(region)
        assert found_screen is screen
        assert logical.width() > 0 and logical.height() > 0
