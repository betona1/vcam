"""편집 타임라인: 눈금, 구간(남길/지울), 나누기 지점, 시작·끝 표시, 재생 위치.

- 클릭/드래그: 재생 위치 이동
- 시작·끝 표시(작은 깃발)를 끌어 선택 구간 조절
- Ctrl+휠: 확대/축소, 휠: 좌우 이동
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from vcam.editing.segments import Segment, format_time
from vcam.ui.tokens import Palette

RULER_H = 20
TRACK_TOP = 26
TRACK_H = 38
MARK_HIT = 8


class Timeline(QWidget):
    seek = Signal(float)
    selection_changed = Signal(float, float)

    def __init__(self, palette_tokens: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self.duration = 0.0
        self.position = 0.0
        self.segments: list[Segment] = []
        self.segment_role = "keep"  # keep | remove | part
        self.split_points: list[float] = []
        self.sel_in: float | None = None
        self.sel_out: float | None = None
        self.view = (0.0, 1.0)  # 보이는 범위(초)
        self._drag: str | None = None
        self.setMinimumHeight(TRACK_TOP + TRACK_H + 14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAccessibleName("타임라인")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, TRACK_TOP + TRACK_H + 14)

    # ── 상태 ───────────────────────────────────────────────────────────────
    def set_palette(self, p: Palette) -> None:
        self.p = p
        self.update()

    def set_duration(self, duration: float) -> None:
        self.duration = max(0.0, duration)
        self.view = (0.0, self.duration or 1.0)
        self.update()

    def set_position(self, t: float) -> None:
        self.position = t
        v0, v1 = self.view
        if self.duration and not (v0 <= t <= v1):  # 확대 중이면 재생 위치를 따라간다
            span = v1 - v0
            start = min(max(0.0, t - span * 0.1), max(0.0, self.duration - span))
            self.view = (start, start + span)
        self.update()

    def set_segments(self, segments: list[Segment], role: str) -> None:
        self.segments, self.segment_role = list(segments), role
        self.update()

    def set_split_points(self, points: list[float]) -> None:
        self.split_points = sorted(points)
        self.update()

    def set_selection(self, sel_in: float | None, sel_out: float | None) -> None:
        self.sel_in, self.sel_out = sel_in, sel_out
        self.update()

    # ── 좌표 ───────────────────────────────────────────────────────────────
    def _track(self) -> QRectF:
        return QRectF(8, TRACK_TOP, self.width() - 16, TRACK_H)

    def x_for(self, t: float) -> float:
        r = self._track()
        v0, v1 = self.view
        return r.left() + (t - v0) / max(1e-9, v1 - v0) * r.width()

    def t_for(self, x: float) -> float:
        r = self._track()
        v0, v1 = self.view
        return min(self.duration, max(0.0, v0 + (x - r.left()) / max(1.0, r.width()) * (v1 - v0)))

    # ── 그리기 ─────────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = self.p
        track = self._track()
        path = QPainterPath()
        path.addRoundedRect(track, 6, 6)
        painter.fillPath(path, QColor(p.surface2))
        if self.duration <= 0:
            painter.setPen(QColor(p.muted))
            painter.drawText(track, Qt.AlignmentFlag.AlignCenter, "동영상을 열면 타임라인이 표시됩니다")
            return
        self._paint_ruler(painter)
        painter.save()
        painter.setClipPath(path)
        colors = {"keep": QColor(p.accent), "remove": QColor(p.rec), "part": QColor(p.cyan)}
        base = colors.get(self.segment_role, QColor(p.accent))
        for i, seg in enumerate(self.segments):
            rect = QRectF(self.x_for(seg.start), track.top(), self.x_for(seg.end) - self.x_for(seg.start), track.height())
            fill = QColor(base)
            fill.setAlpha(110 if self.segment_role != "part" or i % 2 == 0 else 60)
            painter.fillRect(rect, fill)
            if self.segment_role == "remove":
                painter.setPen(QPen(QColor(p.rec), 1.5))
                visible = rect.intersected(track)
                for x in range(int(visible.left()) - int(track.height()), int(visible.right()) + 1, 9):
                    painter.drawLine(QPointF(x, track.bottom()), QPointF(x + track.height(), track.top()))
            painter.setPen(QPen(base, 2))
            painter.drawLine(QPointF(rect.left(), track.top()), QPointF(rect.left(), track.bottom()))
            painter.drawLine(QPointF(rect.right(), track.top()), QPointF(rect.right(), track.bottom()))
        if self.sel_in is not None or self.sel_out is not None:
            a = self.sel_in if self.sel_in is not None else 0.0
            b = self.sel_out if self.sel_out is not None else self.duration
            sel = QColor(p.warn)
            sel.setAlpha(70)
            painter.fillRect(QRectF(self.x_for(a), track.top(), self.x_for(b) - self.x_for(a), track.height()), sel)
        painter.restore()
        for t in self.split_points:
            x = self.x_for(t)
            painter.setPen(QPen(QColor(p.cyan), 2, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, track.top() - 4), QPointF(x, track.bottom() + 4))
        for t, kind in ((self.sel_in, "in"), (self.sel_out, "out")):
            if t is not None:
                self._paint_flag(painter, self.x_for(t), kind)
        x = self.x_for(self.position)
        painter.setPen(QPen(QColor(p.text), 2))
        painter.drawLine(QPointF(x, TRACK_TOP - 6), QPointF(x, track.bottom() + 6))
        painter.setBrush(QColor(p.text))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(x - 6, TRACK_TOP - 12), QPointF(x + 6, TRACK_TOP - 12), QPointF(x, TRACK_TOP - 4)]))

    def _paint_ruler(self, painter: QPainter) -> None:
        v0, v1 = self.view
        span = v1 - v0
        steps = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)
        step = next((s for s in steps if span / s <= max(4, self.width() // 90)), 3600)
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        painter.setFont(f)
        painter.setPen(QColor(self.p.muted))
        t = (int(v0 / step)) * step
        while t <= v1 + 1e-9:
            if t >= v0 - 1e-9:
                x = self.x_for(t)
                painter.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
                painter.drawText(QPointF(x + 3, RULER_H - 7), format_time(t, millis=step < 1))
            t += step

    def _paint_flag(self, painter: QPainter, x: float, kind: str) -> None:
        track = self._track()
        color = QColor(self.p.warn)
        painter.setPen(QPen(color, 2))
        painter.drawLine(QPointF(x, track.top()), QPointF(x, track.bottom() + 8))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        y = track.bottom() + 2
        pts = [QPointF(x, y), QPointF(x + 9, y + 5), QPointF(x, y + 10)] if kind == "in" else \
              [QPointF(x, y), QPointF(x - 9, y + 5), QPointF(x, y + 10)]  # fmt: skip
        painter.drawPolygon(QPolygonF(pts))

    # ── 마우스 ─────────────────────────────────────────────────────────────
    def _hit_mark(self, x: float) -> str | None:
        hits = [(abs(self.x_for(t) - x), kind, t) for t, kind in ((self.sel_in, "in"), (self.sel_out, "out"))
                if t is not None and abs(self.x_for(t) - x) <= MARK_HIT]  # fmt: skip
        if not hits:
            return None
        if len(hits) == 2 and abs(hits[0][2] - hits[1][2]) < 1e-6:
            # 두 깃발이 겹치면 마우스가 오른쪽이면 끝점, 왼쪽이면 시작점을 잡는다
            return "out" if x >= self.x_for(hits[0][2]) else "in"
        return min(hits)[1]

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self.duration <= 0 or e.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = self._hit_mark(e.position().x()) or "seek"
        self._apply_drag(e.position().x())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag:
            self._apply_drag(e.position().x())
        else:
            hit = self._hit_mark(e.position().x())
            self.setCursor(Qt.CursorShape.SizeHorCursor if hit else Qt.CursorShape.PointingHandCursor)
            if self.duration > 0:
                self.setToolTip(format_time(self.t_for(e.position().x())))

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:  # noqa: N802
        self._drag = None

    def _apply_drag(self, x: float) -> None:
        t = self.t_for(x)
        if self._drag == "in":
            self.sel_in = min(t, self.sel_out) if self.sel_out is not None else t
            self.selection_changed.emit(self.sel_in, self.sel_out if self.sel_out is not None else -1.0)
        elif self._drag == "out":
            self.sel_out = max(t, self.sel_in) if self.sel_in is not None else t
            self.selection_changed.emit(self.sel_in if self.sel_in is not None else -1.0, self.sel_out)
        else:
            self.seek.emit(t)
        self.update()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        if self.duration <= 0:
            return
        v0, v1 = self.view
        span = v1 - v0
        steps = e.angleDelta().y() / 120
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            anchor = self.t_for(e.position().x())
            new_span = min(self.duration, max(0.5, span * (0.8 ** steps)))
            ratio = (anchor - v0) / span if span else 0
            start = min(max(0.0, anchor - new_span * ratio), self.duration - new_span)
            self.view = (start, start + new_span)
        else:
            shift = -steps * span * 0.15
            start = min(max(0.0, v0 + shift), max(0.0, self.duration - span))
            self.view = (start, start + span)
        self.update()
        e.accept()
