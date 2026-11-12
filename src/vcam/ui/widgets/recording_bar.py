"""녹화 중 미니 컨트롤 바와 카운트다운 표시.

컨트롤 바는 녹화 영역 안에 놓일 수 있으므로 반드시 캡처에서 제외돼야 한다. 그런데
SetWindowDisplayAffinity는 반투명(WS_EX_LAYERED) 창에는 실패하거나 무시되므로,
바는 WA_TranslucentBackground 대신 setMask로 둥근 모양을 만든다.

바는 평소에는 작은 원형 아이콘(REC 점)으로 접혀 있고, 마우스를 올리면 시간·일시정지·종료 버튼이 펼쳐진다.
마우스가 떠나면 잠시 뒤 다시 접힌다. 녹화를 시작한 직후에는 위치를 알 수 있게 잠깐 펼쳐 보여 준다.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
    QResizeEvent,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from vcam.platform.windows.capture_affinity import exclude_from_capture
from vcam.ui import icons
from vcam.ui.tokens import Palette

COMPACT_SIZE = 40
COLLAPSE_DELAY_MS = 700
INTRO_MS = 2500  # 녹화 시작 직후 펼쳐 보여 주는 시간


class RecordingBar(QWidget):
    pause_clicked = Signal()
    stop_clicked = Signal()
    restore_clicked = Signal()
    mic_clicked = Signal()

    def __init__(self, palette_tokens: Palette) -> None:
        super().__init__(None)
        self.p = palette_tokens
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        # 반투명 배경을 쓰지 않는다 (모듈 설명 참고). 모양은 resizeEvent의 마스크가 만든다.
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
        self.warn_label = QLabel()
        self.warn_label.setAccessibleName("녹화 경고")
        self.warn_label.hide()
        for w in (self.state_label, self.time_label, self.warn_label, self.pause_btn, self.mic_btn, self.stop_btn, self.restore_btn):
            layout.addWidget(w)
        self._layout = layout
        self._drag: QPoint | None = None
        self._paused = False
        self._mic_muted = False
        self._mic_enabled = False
        self._warning = ""
        self._compact = False
        self.mic_btn.hide()
        self._collapse_timer = QTimer(self, singleShot=True)
        self._collapse_timer.timeout.connect(self._collapse_if_idle)
        self.apply_palette(palette_tokens)

    # ── 접기/펼치기 ─────────────────────────────────────────────────────────
    @property
    def compact(self) -> bool:
        return self._compact

    def _full_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = [self.time_label, self.pause_btn, self.stop_btn, self.restore_btn]
        if self._mic_enabled:
            widgets.append(self.mic_btn)
        if self._warning:
            widgets.append(self.warn_label)
        return widgets

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        anchor = self.frameGeometry().center()
        self._compact = compact
        for w in (self.time_label, self.pause_btn, self.stop_btn, self.restore_btn, self.mic_btn, self.warn_label):
            w.setVisible(False)
        if compact:
            self._layout.setContentsMargins(0, 0, 0, 0)
            self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setFixedSize(COMPACT_SIZE, COMPACT_SIZE)
        else:
            for w in self._full_widgets():
                w.setVisible(True)
            self._layout.setContentsMargins(14, 8, 8, 8)
            self.state_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
        self._refresh_state_label()
        self.adjustSize()
        if self.isVisible():
            self._move_centered(anchor)

    def reveal(self, ms: int = 6000) -> None:
        """중요한 경고가 생기면 아이콘을 잠시 펼쳐 내용을 보여 준다."""
        if not self.isVisible():
            return
        self.set_compact(False)
        self._collapse_timer.start(ms)

    def _move_centered(self, center: QPoint) -> None:
        """펼치거나 접을 때 아이콘 자리를 중심으로 두고, 화면 밖으로 나가지 않게 한다."""
        screen = (QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()).availableGeometry()
        x = min(max(screen.left() + 4, center.x() - self.width() // 2), screen.right() - self.width() - 4)
        y = min(max(screen.top() + 4, center.y() - self.height() // 2), screen.bottom() - self.height() - 4)
        self.move(x, y)

    def _collapse_if_idle(self) -> None:
        if self.underMouse() or self._drag is not None:
            self._collapse_timer.start(COLLAPSE_DELAY_MS)
            return
        self.set_compact(True)

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self._collapse_timer.stop()
        self.set_compact(False)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._collapse_timer.start(COLLAPSE_DELAY_MS)
        super().leaveEvent(event)

    def _refresh_state_label(self) -> None:
        if self._compact:
            text = "⚠" if self._warning else ("❚❚" if self._paused else "●")
            color = self.p.warn if (self._warning or self._paused) else self.p.rec
            self.state_label.setText(text)
            self.state_label.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 14pt;")
            tip = f"vCAM {'일시정지' if self._paused else '녹화 중'} {self.time_label.text()}"
            if self._warning:
                tip += f"\n⚠ {self._warning}"
            self.setToolTip(tip + "\n마우스를 올리면 버튼이 나옵니다.")
        else:
            color = self.p.warn if self._paused else self.p.rec
            self.state_label.setText("❚❚ 일시정지" if self._paused else "● REC")
            self.state_label.setStyleSheet(f"color: {color}; font-weight: 700;")
            self.setToolTip("")

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
        self._refresh_state_label()

    def set_mic(self, enabled: bool, muted: bool) -> None:
        self._mic_muted = muted
        self._mic_enabled = enabled
        self.mic_btn.setVisible(enabled and not self._compact)
        self.mic_btn.setIcon(icons.icon("mic_off" if muted else "mic", self.p.warn if muted else self.p.text))
        self.mic_btn.setToolTip("마이크 음소거 해제" if muted else "마이크 음소거")
        self.mic_btn.setAccessibleName(self.mic_btn.toolTip())
        self.adjustSize()

    def set_elapsed(self, text: str) -> None:
        self.time_label.setText(text)
        if self._compact:
            self._refresh_state_label()  # 툴팁의 시간

    def set_warning(self, text: str | None, tip: str = "") -> None:
        """녹화 중 이상 상태(예: 검은 화면)를 바에 짧게 표시한다. None이면 숨긴다."""
        previous, self._warning = self._warning, text or ""
        if not text:
            if not self.warn_label.isHidden():
                self.warn_label.hide()
                self.adjustSize()
            if previous and self._compact:
                self._refresh_state_label()
            return
        self.warn_label.setText(f"⚠ {text}")
        self.warn_label.setToolTip(tip)
        self.warn_label.setStyleSheet(f"color: {self.p.warn}; font-weight: 700;")
        if self._compact:
            if text != previous:
                self._refresh_state_label()
        elif self.warn_label.isHidden():
            self.warn_label.show()
            self.adjustSize()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        path = QPainterPath()
        r = self.height() / 2
        path.addRoundedRect(0, 0, self.width(), self.height(), r, r)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def show_near(self, area: QRect) -> None:
        """녹화 영역과 겹치지 않는 위치(아래 → 위 → 화면 위쪽 가운데)에 띄운다."""
        self.set_compact(False)  # 시작 직후에는 펼쳐 보여 주고 INTRO_MS 뒤 아이콘으로 접는다
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
        self._collapse_timer.start(INTRO_MS)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.p.surface))
        painter.setBrush(QColor(self.p.surface))
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
