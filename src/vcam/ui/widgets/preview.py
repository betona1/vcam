"""선택 대상 미리보기. 캡처 성능을 해치지 않도록 별도 스레드에서 낮은 빈도로 샘플링한다."""

from __future__ import annotations

import logging
import threading

import mss
import numpy as np
from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from vcam.capture.mss_compat import mss_factory
from vcam.domain.models import Rect
from vcam.ui import icons
from vcam.ui.tokens import Palette

log = logging.getLogger(__name__)

PREVIEW_MAX_WIDTH = 720
PREVIEW_INTERVAL_S = 0.5


class PreviewSampler(QObject):
    frame_ready = Signal(object)  # QImage

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rect: Rect | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._paused = False
        self._thread = threading.Thread(target=self._run, name="vcam-preview", daemon=True)
        self._thread.start()

    def set_rect(self, rect: Rect | None) -> None:
        with self._lock:
            self._rect = rect
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._wake.set()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        with mss_factory() as sct:
            while not self._stop.is_set():
                self._wake.wait(PREVIEW_INTERVAL_S)
                self._wake.clear()
                with self._lock:
                    rect = self._rect
                if self._paused or rect is None or rect.is_empty or self._stop.is_set():
                    continue
                try:
                    shot = sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
                except mss.exception.ScreenShotError:
                    log.debug("미리보기 캡처 실패", exc_info=True)
                    continue
                arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
                step = max(1, -(-shot.width // PREVIEW_MAX_WIDTH))
                small = np.ascontiguousarray(arr[::step, ::step])
                h, w = small.shape[:2]
                image = QImage(small.data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
                self.frame_ready.emit(image)


class PreviewView(QWidget):
    def __init__(self, palette_tokens: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self._image: QImage | None = None
        self._overlay_text = ""
        self.setMinimumSize(QSize(360, 220))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("녹화 대상 미리보기")

    def set_palette(self, p: Palette) -> None:
        self.p = p
        self.update()

    @Slot(object)
    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def clear(self) -> None:
        self._image = None
        self.update()

    def set_overlay_text(self, text: str) -> None:
        self._overlay_text = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(outer, 10, 10)
        painter.fillPath(clip, QColor(self.p.surface2))
        if self._image is not None and not self._image.isNull():
            iw, ih = self._image.width(), self._image.height()
            scale = min((outer.width() - 24) / iw, (outer.height() - 24) / ih)
            w, h = iw * scale, ih * scale
            target = QRectF(outer.center().x() - w / 2, outer.center().y() - h / 2, w, h)
            painter.save()
            frame_path = QPainterPath()
            frame_path.addRoundedRect(target, 6, 6)
            painter.setClipPath(frame_path)
            painter.drawImage(target, self._image)
            painter.restore()
            painter.setPen(QPen(QColor(self.p.border), 1))
            painter.drawRoundedRect(target, 6, 6)
        else:
            pm = icons.pixmap("monitor", self.p.muted, 48, self.devicePixelRatioF())
            painter.drawPixmap(int(outer.center().x() - 24), int(outer.center().y() - 40), pm)
            painter.setPen(QColor(self.p.muted))
            painter.drawText(outer.adjusted(0, 40, 0, 0), Qt.AlignmentFlag.AlignCenter, "녹화할 화면이나 영역을 선택하세요")
        if self._overlay_text:
            f = QFont(self.font())
            f.setPointSizeF(11)
            f.setBold(True)
            painter.setFont(f)
            w = painter.fontMetrics().horizontalAdvance(self._overlay_text) + 32
            box = QRectF(outer.center().x() - w / 2, outer.top() + 16, w, 32)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.p.rec))
            painter.drawRoundedRect(box, 16, 16)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self._overlay_text)
