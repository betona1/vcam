"""녹화 중 미니 컨트롤 바와 카운트다운 표시. 둘 다 녹화 결과에서 제외된다."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from vcam.platform.windows.capture_affinity import exclude_from_capture
from vcam.ui import icons
from vcam.ui.tokens import Palette


class RecordingBar(QWidget):
    pause_clicked = Signal()
    stop_clicked = Signal()
    restore_clicked = Signal()
    mic_clicked = Signal()

    def __init__(self, palette_tokens: Palette) -> None:
        super().__init__(None)
        self.p = palette_tokens
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAccessibleName("녹화 컨트롤")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 8, 8)
        layout.setSpacing(6)
        self.state_label = QLabel("● REC")
        self.time_label = QLabel("00:00")
        self.time_label.setMinimumWidth(64)
        self.pause_btn = self._button("pause", "일시정지 (F10)", self.pause_clicked)
        self.mic_btn = self._button("mic", "마이크 음소거", self.mic_clicked)
        self.stop_btn = self._button("stop", "녹화 종료 (F9)", self.stop_clicked)
        self.restore_btn = self._button("film", "vcam 창 열기", self.restore_clicked)
        for w in (self.state_label, self.time_label, self.pause_btn, self.mic_btn, self.stop_btn, self.restore_btn):
            layout.addWidget(w)
        self._drag: QPoint | None = None
        self._paused = False
        self._mic_muted = False
        self.mic_btn.hide()
        self.apply_palette(palette_tokens)

    def _button(self, name: str, tip: str, signal) -> QToolButton:
        b = QToolButton(self)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setIconSize(QSize(18, 18))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(signal.emit)
        b.setProperty("icon_name", name)
        return b

    def apply_palette(self, p: Palette) -> None:
        self.p = p
        self.setStyleSheet(
            f"QLabel {{ color: {p.text}; font-weight: 600; }}"
            f"QToolButton {{ border-radius: 14px; padding: 5px; background: transparent; border: none; }}"
            f"QToolButton:hover {{ background: {p.surface2}; }}"
        )
        self.time_label.setStyleSheet(f"color: {p.text}; font-family: 'Cascadia Mono','Consolas'; font-size: 11pt;")
        self.pause_btn.setIcon(icons.icon("play" if self._paused else "pause", p.text))
        self.stop_btn.setIcon(icons.icon("stop", p.rec))
        self.restore_btn.setIcon(icons.icon("film", p.muted))
        self.set_paused(self._paused)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_btn.setIcon(icons.icon("play" if paused else "pause", self.p.text))
        self.pause_btn.setToolTip("재개 (F10)" if paused else "일시정지 (F10)")
        color = self.p.warn if paused else self.p.rec
        self.state_label.setText("❚❚ 일시정지" if paused else "● REC")
        self.state_label.setStyleSheet(f"color: {color}; font-weight: 700;")

    def set_mic(self, enabled: bool, muted: bool) -> None:
        self._mic_muted = muted
        self.mic_btn.setVisible(enabled)
        self.mic_btn.setIcon(icons.icon("mic_off" if muted else "mic", self.p.warn if muted else self.p.text))
        self.mic_btn.setToolTip("마이크 음소거 해제" if muted else "마이크 음소거")
        self.mic_btn.setAccessibleName(self.mic_btn.toolTip())
        self.adjustSize()

    def set_elapsed(self, text: str) -> None:
        self.time_label.setText(text)

    def show_near(self, area: QRect) -> None:
        """녹화 영역과 겹치지 않는 위치(아래 → 위 → 화면 위쪽 가운데)에 띄운다."""
        self.adjustSize()
        target = QGuiApplication.screenAt(area.center()) or QGuiApplication.primaryScreen()
        screen = target.availableGeometry()
        w, h = self.width(), self.height()
        x = min(max(screen.left() + 8, area.center().x() - w // 2), screen.right() - w - 8)
        if area.bottom() + 24 + h < screen.bottom():
            y = area.bottom() + 24
        elif area.top() - 24 - h > screen.top():
            y = area.top() - 24 - h
        else:
            y = screen.top() + 12
        self.move(x, y)
        self.show()
        exclude_from_capture(int(self.winId()))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self.p.surface)
        bg.setAlpha(245)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(self.p.border), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.height() / 2, self.height() / 2)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:  # noqa: N802
        self._drag = None


class CountdownOverlay(QWidget):
    cancel_clicked = Signal()

    def __init__(self, palette_tokens: Palette) -> None:
        super().__init__(None)
        self.p = palette_tokens
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(180, 180)
        self._value = 3

    def show_value(self, value: int, area: QRect) -> None:
        self._value = value
        self.move(area.center() - QPoint(self.width() // 2, self.height() // 2))
        if not self.isVisible():
            self.show()
            exclude_from_capture(int(self.winId()))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self.p.surface)
        bg.setAlpha(230)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(self.p.accent), 3))
        painter.drawEllipse(QRectF(self.rect()).adjusted(6, 6, -6, -6))
        f = QFont(self.font())
        f.setPointSize(54)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(self.p.text))
        painter.drawText(self.rect().adjusted(0, -12, 0, -12), Qt.AlignmentFlag.AlignCenter, str(self._value))
        f.setPointSize(9)
        f.setBold(False)
        painter.setFont(f)
        painter.setPen(QColor(self.p.muted))
        painter.drawText(self.rect().adjusted(0, 100, 0, -24), Qt.AlignmentFlag.AlignCenter, "F9 취소")

    def mousePressEvent(self, _e: QMouseEvent) -> None:  # noqa: N802
        self.cancel_clicked.emit()
