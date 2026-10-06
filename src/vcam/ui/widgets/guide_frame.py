"""바탕화면 위에 떠 있는 녹화 영역 가이드 프레임.

- 녹화 영역의 바깥쪽 여백에만 테두리·코너 가이드·핸들을 그리므로 결과 영상에 찍히지 않는다.
  (지원 OS에서는 캡처 제외 속성도 함께 건다.)
- 준비 상태: 모서리/변을 끌어 크기 조절, 위쪽 탭을 끌어 이동. 영역 안쪽은 클릭이 아래 창으로 통과한다.
- 녹화 상태: 빨간 테두리로 잠기고 마우스 입력을 받지 않는다.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from vcam.capture.region import MIN_REGION
from vcam.domain.models import Rect
from vcam.platform.windows.capture_affinity import exclude_from_capture
from vcam.platform.windows.dpi import ScreenMapper
from vcam.ui import icons
from vcam.ui.tokens import Palette
from vcam.ui.widgets.region_overlay import resize_rect

MARGIN = 14  # 영역 바깥 여백(논리 px) — 여기에만 그린다
TAB_H = 30
_EDGE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    "tab": Qt.CursorShape.SizeAllCursor,
}  # fmt: skip


class GuideFrame(QWidget):
    region_changed = Signal(object)  # Rect (물리 픽셀)
    reselect_requested = Signal()

    def __init__(self, palette_tokens: Palette, mapper: ScreenMapper) -> None:
        super().__init__(None)
        self.p = palette_tokens
        self.mapper = mapper
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._region: Rect | None = None
        self._inner = QRectF()  # 전역 논리 좌표의 녹화 영역
        self._tab_above = True
        self._locked = False
        self._status = ""
        self._mode: str | None = None
        self._press = QPointF()
        self._press_inner = QRectF()

    def set_palette(self, palette_tokens: Palette) -> None:
        self.p = palette_tokens
        self.update()

    # ── 외부 API ───────────────────────────────────────────────────────────
    def show_region(self, region: Rect) -> None:
        self._region = region
        _screen, logical = self.mapper.rect_to_logical(region)
        self._inner = QRectF(logical)
        self._relayout()
        if not self.isVisible():
            self.show()
            exclude_from_capture(int(self.winId()))
        self.update()

    def set_locked(self, locked: bool, status: str = "") -> None:
        self._locked = locked
        self._status = status
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
        self.update()

    def set_status(self, status: str) -> None:
        if status != self._status:
            self._status = status
            self.update()

    # ── 배치 ───────────────────────────────────────────────────────────────
    def _screen_bounds(self) -> QRectF:
        screen = QGuiApplication.screenAt(self._inner.center().toPoint()) or QGuiApplication.primaryScreen()
        return QRectF(screen.geometry())

    def _relayout(self) -> None:
        bounds = self._screen_bounds()
        self._tab_above = self._inner.top() - MARGIN - TAB_H >= bounds.top()
        outer = self._inner.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        if self._tab_above:
            outer.setTop(outer.top() - TAB_H)
        else:
            outer.setBottom(outer.bottom() + TAB_H)
        self.setGeometry(outer.toAlignedRect())

    def _local_inner(self) -> QRectF:
        return self._inner.translated(-QPointF(self.geometry().topLeft()))

    def _tab_rect(self) -> QRectF:
        r = self._local_inner()
        w = min(250.0, float(self.width()) - 4)
        y = r.top() - MARGIN - TAB_H + 2 if self._tab_above else r.bottom() + MARGIN - 2
        return QRectF(r.left() - MARGIN + 2, y, w, TAB_H - 4)

    # ── 그리기 ─────────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._local_inner()
        line = QColor(self.p.rec if self._locked else self.p.accent)
        guide = QColor(self.p.rec if self._locked else self.p.cyan)

        # 여백 영역을 거의 투명하게 칠해 마우스를 받는다(안쪽 녹화 영역은 비워 클릭이 통과).
        if not self._locked:
            hit = QPainterPath()
            hit.addRect(QRectF(self.rect()))
            inner = QPainterPath()
            inner.addRect(r)
            painter.fillPath(hit.subtracted(inner), QColor(0, 0, 0, 1))

        # 테두리는 영역 바로 바깥(2px 띄움)에 그린다.
        border = r.adjusted(-3, -3, 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(line, 2, Qt.PenStyle.DashLine if not self._locked else Qt.PenStyle.SolidLine))
        painter.drawRect(border)

        # 코너 가이드(ㄱ자) — 바깥쪽으로
        g = r.adjusted(-8, -8, 8, 8)
        size = min(28.0, r.width() / 3, r.height() / 3)
        painter.setPen(QPen(guide, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        path = QPainterPath()
        for cx, cy, dx, dy in ((g.left(), g.top(), 1, 1), (g.right(), g.top(), -1, 1),
                               (g.right(), g.bottom(), -1, -1), (g.left(), g.bottom(), 1, -1)):  # fmt: skip
            path.moveTo(cx + dx * size, cy)
            path.lineTo(cx, cy)
            path.lineTo(cx, cy + dy * size)
        painter.drawPath(path)

        # 변 중앙 핸들
        if not self._locked:
            painter.setPen(QPen(line, 1.5))
            painter.setBrush(QColor("#ffffff"))
            c = r.center()
            for pt in (QPointF(c.x(), g.top()), QPointF(c.x(), g.bottom()), QPointF(g.left(), c.y()), QPointF(g.right(), c.y())):
                painter.drawRoundedRect(QRectF(pt.x() - 6, pt.y() - 4, 12, 8) if pt.y() in (g.top(), g.bottom())
                                        else QRectF(pt.x() - 4, pt.y() - 6, 8, 12), 2, 2)  # fmt: skip
        self._paint_tab(painter, line)

    def _paint_tab(self, painter: QPainter, line: QColor) -> None:
        tab = self._tab_rect()
        bg = QColor(self.p.surface)
        bg.setAlpha(240)
        painter.setPen(QPen(line, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(tab, 8, 8)
        x = tab.left() + 6
        icon_name = "record" if self._locked else "grip"
        icon_color = self.p.rec if self._locked else self.p.muted
        pm = icons.pixmap(icon_name, icon_color, 16, self.devicePixelRatioF())
        painter.drawPixmap(QPointF(x, tab.center().y() - 8), pm)
        x += 22
        f = QFont(self.font())
        f.setPointSizeF(9)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(self.p.text))
        size_text = f"{self._region.width} × {self._region.height}" if self._region else ""
        text = f"{self._status}   {size_text}" if self._status else f"녹화 영역   {size_text}"
        painter.drawText(QRectF(x, tab.top(), tab.right() - x - 6, tab.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)  # fmt: skip

    # ── 마우스 ─────────────────────────────────────────────────────────────
    def _hit(self, pos: QPointF) -> str | None:
        if self._tab_rect().contains(pos):
            return "tab"
        r = self._local_inner()
        if r.contains(pos):
            return None
        tol = MARGIN
        left = abs(pos.x() - r.left()) <= tol and pos.x() < r.left() + 2
        right = abs(pos.x() - r.right()) <= tol and pos.x() > r.right() - 2
        top = abs(pos.y() - r.top()) <= tol and pos.y() < r.top() + 2
        bottom = abs(pos.y() - r.bottom()) <= tol and pos.y() > r.bottom() - 2
        mode = ("n" if top else "s" if bottom else "") + ("w" if left else "e" if right else "")
        return mode or None

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._locked or e.button() != Qt.MouseButton.LeftButton:
            return
        self._mode = self._hit(e.position())
        self._press = e.globalPosition()
        self._press_inner = QRectF(self._inner)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if not self._locked and self._tab_rect().contains(e.position()):
            self.reselect_requested.emit()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._mode is None:
            hit = self._hit(e.position())
            self.setCursor(_EDGE_CURSORS.get(hit, Qt.CursorShape.ArrowCursor))
            return
        delta = e.globalPosition() - self._press
        bounds = self._screen_bounds()
        if self._mode == "tab":
            r = self._press_inner.translated(delta)
            x = min(max(bounds.left(), r.left()), bounds.right() - r.width())
            y = min(max(bounds.top(), r.top()), bounds.bottom() - r.height())
            self._inner = QRectF(x, y, r.width(), r.height())
        else:
            r = resize_rect(self._press_inner, self._mode, delta)
            minimum = MIN_REGION / max(1.0, self.devicePixelRatioF())
            if r.width() >= minimum and r.height() >= minimum:
                self._inner = r.intersected(bounds)
        self._region = self._physical()
        self._relayout()
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._mode is not None and e.button() == Qt.MouseButton.LeftButton:
            self._mode = None
            region = self._physical()
            if region is not None:
                self._region = region
                self.region_changed.emit(region)

    def _physical(self) -> Rect | None:
        screen = QGuiApplication.screenAt(self._inner.center().toPoint()) or QGuiApplication.primaryScreen()
        x1, y1 = self.mapper.to_physical(screen, self._inner.topLeft())
        x2, y2 = self.mapper.to_physical(screen, self._inner.bottomRight())
        rect = Rect.from_points(x1, y1, x2, y2).intersected(self.mapper.physical_rect(screen))
        return None if rect.is_empty else rect

