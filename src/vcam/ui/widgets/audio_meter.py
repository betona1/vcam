"""가로 음량 미터: RMS 막대 + 피크 눈금. 색만으로 구분하지 않도록 dB 툴팁과 상태 문구를 함께 쓴다."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from vcam.audio.meter import SILENCE_DB, Level
from vcam.ui.tokens import Palette

FLOOR_DB = -60.0


def db_to_ratio(db: float) -> float:
    return min(1.0, max(0.0, (db - FLOOR_DB) / -FLOOR_DB))


class AudioMeter(QWidget):
    def __init__(self, palette_tokens: Palette, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self._label = label
        self._level: Level | None = None
        self._status = ""
        self.setMinimumHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(f"{label} 음량")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, 18)

    def set_palette(self, p: Palette) -> None:
        self.p = p
        self.update()

    def set_level(self, level: Level | None, status: str = "") -> None:
        if level == self._level and status == self._status:
            return
        self._level, self._status = level, status
        if status:
            self.setToolTip(status)
        elif level is None:
            self.setToolTip("꺼짐")
        else:
            peak = "무음" if level.peak_db <= SILENCE_DB else f"{level.peak_db:.0f} dBFS"
            self.setToolTip(f"{self._label} 피크 {peak}")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 3.5, -0.5, -3.5)
        track = QPainterPath()
        track.addRoundedRect(r, r.height() / 2, r.height() / 2)
        painter.fillPath(track, QColor(self.p.surface2))
        if self._status or self._level is None:
            painter.setPen(QColor(self.p.muted))
            f = painter.font()
            f.setPointSizeF(8)
            painter.setFont(f)
            text = self._status or "꺼짐"
            painter.drawText(QRectF(self.rect()).adjusted(8, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)
            return
        painter.setClipPath(track)
        grad = QLinearGradient(r.left(), 0, r.right(), 0)
        grad.setColorAt(0.0, QColor(self.p.ok))
        grad.setColorAt(0.70, QColor(self.p.ok))
        grad.setColorAt(0.85, QColor(self.p.warn))
        grad.setColorAt(1.0, QColor(self.p.rec))
        fill = QRectF(r.left(), r.top(), r.width() * db_to_ratio(self._level.rms_db + 6), r.height())
        painter.fillRect(fill, grad)
        peak_x = r.left() + r.width() * db_to_ratio(self._level.peak_db)
        if self._level.peak_db > SILENCE_DB:
            painter.fillRect(QRectF(peak_x - 1.5, r.top(), 3, r.height()), QColor(self.p.text))
        # -12/-6 dB 눈금
        for db in (-12.0, -6.0):
            x = r.left() + r.width() * db_to_ratio(db)
            painter.fillRect(QRectF(x, r.top(), 1, r.height()), QColor(self.p.bg))
