"""영역 선택 오버레이.

모든 모니터에 반투명 딤을 깔고, 드래그한 영역만 밝게 보여준다. 8개 핸들로 크기를 바꾸고
안쪽을 끌어 옮기며, Enter/더블클릭으로 확정, Esc로 취소한다. 영역은 한 모니터 안으로 제한한다
(서로 다른 DPI가 섞인 경계를 넘는 영역은 물리 픽셀을 정확히 보장할 수 없기 때문).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QScreen,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton, QWidget

from vcam.capture.region import ASPECT_PRESETS, MIN_REGION, SIZE_PRESETS
from vcam.domain.models import Rect
from vcam.platform.windows.dpi import ScreenMapper
from vcam.ui import icons
from vcam.ui.tokens import Palette

HANDLE = 9
SNAP = 8
_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}  # fmt: skip


def handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {
        "nw": r.topLeft(), "n": QPointF(cx, r.top()), "ne": r.topRight(), "e": QPointF(r.right(), cy),
        "se": r.bottomRight(), "s": QPointF(cx, r.bottom()), "sw": r.bottomLeft(), "w": QPointF(r.left(), cy),
    }  # fmt: skip


def hit_test(r: QRectF, pos: QPointF, tolerance: float = HANDLE) -> str | None:
    for name, pt in handle_points(r).items():
        if abs(pt.x() - pos.x()) <= tolerance and abs(pt.y() - pos.y()) <= tolerance:
            return name
    if r.contains(pos):
        return "move"
    return None


def resize_rect(start: QRectF, mode: str, delta: QPointF) -> QRectF:
    r = QRectF(start)
    if "w" in mode:
        r.setLeft(min(r.left() + delta.x(), r.right() - MIN_REGION / 2))
    if "e" in mode:
        r.setRight(max(r.right() + delta.x(), r.left() + MIN_REGION / 2))
    if "n" in mode:
        r.setTop(min(r.top() + delta.y(), r.bottom() - MIN_REGION / 2))
    if "s" in mode:
        r.setBottom(max(r.bottom() + delta.y(), r.top() + MIN_REGION / 2))
    return r


def clamp_into(r: QRectF, bounds: QRectF, keep_size: bool) -> QRectF:
    r = QRectF(r)
    if keep_size:
        w, h = min(r.width(), bounds.width()), min(r.height(), bounds.height())
        x = min(max(bounds.left(), r.left()), bounds.right() - w)
        y = min(max(bounds.top(), r.top()), bounds.bottom() - h)
        return QRectF(x, y, w, h)
    return r.intersected(bounds)


def snap_to(r: QRectF, bounds: QRectF) -> QRectF:
    r = QRectF(r)
    if abs(r.left() - bounds.left()) < SNAP:
        r.setLeft(bounds.left())
    if abs(r.top() - bounds.top()) < SNAP:
        r.setTop(bounds.top())
    if abs(bounds.right() - r.right()) < SNAP:
        r.setRight(bounds.right())
    if abs(bounds.bottom() - r.bottom()) < SNAP:
        r.setBottom(bounds.bottom())
    return r


class _Toolbar(QFrame):
    def __init__(self, selector: RegionSelector, parent: QWidget) -> None:
        super().__init__(parent)
        p = selector.palette_tokens
        self.setStyleSheet(
            f"QFrame {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}"
            f"QToolButton {{ color: {p.text}; padding: 6px 10px; border-radius: 6px; border: none; }}"
            f"QToolButton:hover {{ background: {p.surface2}; }}"
            f"QToolButton#ok {{ background: {p.accent}; color: {p.accent_text}; font-weight: 600; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        def button(text: str, icon_name: str, color: str, slot, name: str = "") -> QToolButton:
            b = QToolButton(self)
            b.setText(text)
            b.setIcon(icons.icon(icon_name, color))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if name:
                b.setObjectName(name)
            b.clicked.connect(slot)
            layout.addWidget(b)
            return b

        presets = button("크기", "resize", p.text, lambda: None)
        presets.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        presets.setMenu(selector.build_preset_menu(self))
        button("취소", "close", p.text, selector.cancel)
        button("이 영역으로", "check", p.accent_text, selector.confirm, "ok")
        self.adjustSize()


class _ScreenOverlay(QWidget):
    def __init__(self, selector: RegionSelector, screen: QScreen) -> None:
        super().__init__(None)
        self.selector = selector
        self.target_screen = screen
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(screen.geometry())
        self.toolbar = _Toolbar(selector, self)
        self.toolbar.hide()

    def local(self, r: QRectF) -> QRectF:
        return r.translated(-QPointF(self.geometry().topLeft()))

    def place_toolbar(self) -> None:
        sel = self.selector.selection if self.selector.screen is self.target_screen else None
        if sel is None:
            self.toolbar.hide()
            return
        r = self.local(sel)
        tb = self.toolbar.sizeHint()
        x = min(max(4, r.right() - tb.width()), self.width() - tb.width() - 4)
        y = r.bottom() + 10
        if y + tb.height() > self.height() - 4:
            y = r.top() - tb.height() - 10
        if y < 4:
            y = r.bottom() - tb.height() - 10
        self.toolbar.setGeometry(QRect(int(x), int(y), tb.width(), tb.height()))
        self.toolbar.show()
        self.toolbar.raise_()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = self.selector.palette_tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dim = QColor(p.dim)
        dim.setAlpha(150)
        painter.fillRect(self.rect(), dim)
        sel = self.selector.selection if self.selector.screen is self.target_screen else None
        if sel is None:
            self._draw_hint(painter, p)
            return
        r = self.local(sel)
        # 안쪽은 보이지 않을 만큼만(알파 1) 칠해 클릭을 받는다. 알파 0이면 클릭이 아래 창으로 통과한다.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(r, QColor(0, 0, 0, 1))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        draw_guides(painter, r, QColor(p.accent), QColor(p.cyan), handles=True)
        self._draw_size_label(painter, r, p)

    def _draw_hint(self, painter: QPainter, p: Palette) -> None:
        text = "드래그해서 녹화할 영역을 지정하세요"
        sub = "Enter 확정 · Esc 취소 · 우클릭 크기 프리셋"
        center = self.rect().center()
        box = QRectF(center.x() - 220, center.y() - 44, 440, 88)
        painter.setPen(Qt.PenStyle.NoPen)
        bg = QColor(p.surface)
        bg.setAlpha(235)
        painter.setBrush(bg)
        painter.drawRoundedRect(box, 14, 14)
        painter.setPen(QColor(p.text))
        f = QFont(self.font())
        f.setPointSizeF(12.5)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(box.adjusted(0, 14, 0, -40), Qt.AlignmentFlag.AlignCenter, text)
        f.setPointSizeF(9.5)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor(p.muted))
        painter.drawText(box.adjusted(0, 46, 0, -10), Qt.AlignmentFlag.AlignCenter, sub)

    def _draw_size_label(self, painter: QPainter, r: QRectF, p: Palette) -> None:
        phys = self.selector.physical_selection()
        if phys is None:
            return
        text = f"{phys.width} × {phys.height}"
        f = QFont(self.font())
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        w = painter.fontMetrics().horizontalAdvance(text) + 18
        box = QRectF(r.left(), r.top() - 30, w, 24)
        if box.top() < 2:
            box.moveTop(r.top() + 6)
            box.moveLeft(r.left() + 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.accent))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor(p.accent_text))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.RightButton:
            self.selector.build_preset_menu(self, self.target_screen).exec(e.globalPosition().toPoint())
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.selector.press(self.target_screen, e.globalPosition())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self.selector.move(self.target_screen, e.globalPosition(), self)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.selector.release()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        sel = self.selector.selection
        if sel is not None and self.selector.screen is self.target_screen and sel.contains(e.globalPosition()):
            self.selector.confirm()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        self.selector.key(e)


def draw_guides(painter: QPainter, r: QRectF, accent: QColor, cyan: QColor, handles: bool) -> None:
    """선택 테두리 + 코너 브래킷 + 3분할 가이드라인 + 핸들."""
    painter.setBrush(Qt.BrushStyle.NoBrush)
    thirds = QColor(255, 255, 255, 70)
    painter.setPen(QPen(thirds, 1, Qt.PenStyle.DashLine))
    for i in (1, 2):
        x = r.left() + r.width() * i / 3
        y = r.top() + r.height() * i / 3
        painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
    painter.setPen(QPen(accent, 2))
    painter.drawRect(r)
    bracket = min(24.0, r.width() / 4, r.height() / 4)
    painter.setPen(QPen(cyan, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
    path = QPainterPath()
    for cx, cy, dx, dy in (
        (r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
        (r.right(), r.bottom(), -1, -1), (r.left(), r.bottom(), 1, -1),
    ):  # fmt: skip
        path.moveTo(cx + dx * bracket, cy)
        path.lineTo(cx, cy)
        path.lineTo(cx, cy + dy * bracket)
    painter.drawPath(path)
    if handles:
        painter.setPen(QPen(accent, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for pt in handle_points(r).values():
            painter.drawRoundedRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10), 2, 2)
        c = r.center()
        if r.width() > 60 and r.height() > 60:
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 200))
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.drawEllipse(c, 12, 12)
            painter.drawLine(QPointF(c.x() - 5, c.y()), QPointF(c.x() + 5, c.y()))
            painter.drawLine(QPointF(c.x(), c.y() - 5), QPointF(c.x(), c.y() + 5))


class RegionSelector(QWidget):
    """여러 화면의 오버레이를 묶어 관리한다. 자신은 보이지 않는 조정자 위젯이다."""

    selected = Signal(object)  # Rect (물리 픽셀)
    cancelled = Signal()

    def __init__(self, palette_tokens: Palette, mapper: ScreenMapper, initial: Rect | None = None) -> None:
        super().__init__(None)
        self.palette_tokens = palette_tokens
        self.mapper = mapper
        self.screen: QScreen | None = None
        self.selection: QRectF | None = None
        self.last_region = initial
        self._mode: str | None = None
        self._press_pos = QPointF()
        self._press_rect = QRectF()
        self._overlays: list[_ScreenOverlay] = []
        if initial is not None and not initial.is_empty:
            screen, logical = mapper.rect_to_logical(initial)
            self.screen = screen
            self.selection = QRectF(logical)

    def open(self) -> None:
        for screen in QGuiApplication.screens():
            overlay = _ScreenOverlay(self, screen)
            self._overlays.append(overlay)
            overlay.show()
            overlay.place_toolbar()
        target = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        for overlay in self._overlays:
            if overlay.target_screen is target:
                overlay.activateWindow()
                overlay.raise_()
                overlay.setFocus()

    def close_all(self) -> None:
        for overlay in self._overlays:
            overlay.close()
            overlay.deleteLater()
        self._overlays.clear()

    def physical_selection(self) -> Rect | None:
        if self.selection is None or self.screen is None:
            return None
        x1, y1 = self.mapper.to_physical(self.screen, self.selection.topLeft())
        x2, y2 = self.mapper.to_physical(self.screen, self.selection.bottomRight())
        bounds = self.mapper.physical_rect(self.screen)
        rect = Rect.from_points(x1, y1, x2, y2).intersected(bounds)
        return None if rect.is_empty else rect

    def _update(self) -> None:
        for overlay in self._overlays:
            overlay.update()
            overlay.place_toolbar()

    # ── 상호작용 ───────────────────────────────────────────────────────────
    def press(self, screen: QScreen, pos: QPointF) -> None:
        mode = hit_test(self.selection, pos) if (self.selection and self.screen is screen) else None
        if mode is None:
            self.screen = screen
            self.selection = QRectF(pos, pos)
            mode = "new"
        self._mode = mode
        self._press_pos = pos
        self._press_rect = QRectF(self.selection)
        self._update()

    def move(self, screen: QScreen, pos: QPointF, overlay: QWidget) -> None:
        if self._mode is None:
            hit = hit_test(self.selection, pos) if (self.selection and self.screen is screen) else None
            overlay.setCursor(_CURSORS.get(hit, Qt.CursorShape.CrossCursor))
            return
        assert self.screen is not None
        bounds = QRectF(self.screen.geometry())
        delta = pos - self._press_pos
        if self._mode == "new":
            r = QRectF(self._press_pos, pos).normalized()
            self.selection = snap_to(clamp_into(r, bounds, keep_size=False), bounds)
        elif self._mode == "move":
            self.selection = clamp_into(self._press_rect.translated(delta), bounds, keep_size=True)
        else:
            r = resize_rect(self._press_rect, self._mode, delta)
            self.selection = snap_to(clamp_into(r, bounds, keep_size=False), bounds)
        self._update()

    def release(self) -> None:
        too_small = self.selection is not None and (self.selection.width() < 8 or self.selection.height() < 8)
        if self._mode == "new" and too_small:
            # 클릭만 한 경우: 그 모니터 전체를 선택한다.
            self.selection = QRectF(self.screen.geometry())
        self._mode = None
        self._update()

    def key(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
        elif self.selection is not None and self.screen is not None and e.key() in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
        ):  # fmt: skip
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(e.key(), 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(e.key(), 0)
            bounds = QRectF(self.screen.geometry())
            self.selection = clamp_into(self.selection.translated(dx, dy), bounds, keep_size=True)
            self._update()

    def confirm(self) -> None:
        rect = self.physical_selection()
        if rect is None or rect.width < MIN_REGION or rect.height < MIN_REGION:
            return
        self.close_all()
        self.selected.emit(rect)

    def cancel(self) -> None:
        self.close_all()
        self.cancelled.emit()

    # ── 프리셋 ─────────────────────────────────────────────────────────────
    def build_preset_menu(self, parent: QWidget, screen: QScreen | None = None) -> QMenu:
        menu = QMenu(parent)
        menu.addAction("이 모니터 전체", lambda: self._apply_full(screen))
        if self.last_region is not None:
            menu.addAction("마지막 영역", self._apply_last)
        menu.addSeparator()
        for label, w, h in SIZE_PRESETS:
            menu.addAction(label, lambda w=w, h=h: self._apply_size(screen, w, h))
        menu.addSeparator()
        for label, aw, ah in ASPECT_PRESETS:
            menu.addAction(f"비율 {label}", lambda aw=aw, ah=ah: self._apply_aspect(screen, aw, ah))
        return menu

    def _target_screen(self, screen: QScreen | None) -> QScreen:
        return screen or self.screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _apply_full(self, screen: QScreen | None) -> None:
        self.screen = self._target_screen(screen)
        self.selection = QRectF(self.screen.geometry())
        self._update()

    def _apply_last(self) -> None:
        if self.last_region is None:
            return
        screen, logical = self.mapper.rect_to_logical(self.last_region)
        self.screen, self.selection = screen, QRectF(logical)
        self._update()

    def _apply_size(self, screen: QScreen | None, w: int, h: int) -> None:
        """물리 픽셀 기준 크기를 화면 배율로 환산해 현재 영역 중심(없으면 화면 중심)에 놓는다."""
        self.screen = self._target_screen(screen)
        scale = self.mapper.scale(self.screen)
        bounds = QRectF(self.screen.geometry())
        lw, lh = w / scale, h / scale
        fit = min(1.0, bounds.width() / lw, bounds.height() / lh)
        lw, lh = lw * fit, lh * fit
        center = self.selection.center() if self.selection is not None else bounds.center()
        r = QRectF(center.x() - lw / 2, center.y() - lh / 2, lw, lh)
        self.selection = clamp_into(r, bounds, keep_size=True)
        self._update()

    def _apply_aspect(self, screen: QScreen | None, aw: int, ah: int) -> None:
        self.screen = self._target_screen(screen)
        bounds = QRectF(self.screen.geometry())
        base = self.selection or QRectF(bounds.center() - QPointF(320, 180), bounds.center() + QPointF(320, 180))
        w = base.width()
        h = w * ah / aw
        if h > bounds.height():
            h = bounds.height()
            w = h * aw / ah
        c = base.center()
        self.selection = clamp_into(QRectF(c.x() - w / 2, c.y() - h / 2, w, h), bounds, keep_size=True)
        self._update()


