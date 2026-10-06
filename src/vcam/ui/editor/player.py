"""미리보기 플레이어(Qt Multimedia). 재생, 프레임 단위 이동, 위치 표시."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget

from vcam.editing.segments import format_time
from vcam.ui import icons
from vcam.ui.tokens import Palette


class Player(QWidget):
    position_changed = Signal(float)  # 초
    capture_requested = Signal()

    def __init__(self, palette_tokens: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self.fps = 30.0
        self.duration = 0.0
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self._audio)
        self.video = QVideoWidget(self)
        self.video.setMinimumSize(QSize(480, 270))
        self.video.setStyleSheet("background: #000;")
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(lambda ms: self._on_position(self.position() if self._target is not None else ms / 1000))
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.playbackStateChanged.connect(lambda _s: self._sync_play_icon())

        self.play_btn = self._btn("play", "재생 / 일시정지 (Space)", self.toggle_play, 22)
        self.prev_btn = self._btn("frame_prev", "이전 프레임 (←)", lambda: self.step(-1))
        self.next_btn = self._btn("frame_next", "다음 프레임 (→)", lambda: self.step(1))
        self.back_btn = self._btn("back5", "5초 뒤로 (Shift+←)", lambda: self.jump(-5))
        self.fwd_btn = self._btn("fwd5", "5초 앞으로 (Shift+→)", lambda: self.jump(5))
        self.capture_btn = self._btn("camera", "현재 프레임을 PNG로 저장", self.capture_requested.emit)
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setStyleSheet("font-family: 'Cascadia Mono','Consolas'; font-size: 10pt;")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.volume.setAccessibleName("미리보기 음량")
        self.volume.valueChanged.connect(lambda v: self._audio.setVolume(v / 100))
        self._vol_icon = QLabel()

        controls = QHBoxLayout()
        controls.setSpacing(4)
        for w in (self.back_btn, self.prev_btn, self.play_btn, self.next_btn, self.fwd_btn):
            controls.addWidget(w)
        controls.addSpacing(8)
        controls.addWidget(self.time_label)
        controls.addStretch(1)
        controls.addWidget(self.capture_btn)
        controls.addSpacing(8)
        controls.addWidget(self._vol_icon)
        controls.addWidget(self.volume)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.video, 1)
        layout.addLayout(controls)
        self.apply_palette(palette_tokens)
        self._pending_seek: float | None = None
        self._target: float | None = None  # 마지막으로 요청한 위치(백엔드가 따라올 때까지)
        self._target_timer = QTimer(self, singleShot=True, interval=400)
        self._target_timer.timeout.connect(self._clear_target)
        self._seek_timer = QTimer(self, singleShot=True, interval=30)
        self._seek_timer.timeout.connect(self._flush_seek)

    def _btn(self, name: str, tip: str, slot, size: int = 18) -> QToolButton:
        b = QToolButton(self)
        b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setIconSize(QSize(size, size))
        b.setProperty("icon_name", name)
        b.clicked.connect(slot)
        return b

    def apply_palette(self, p: Palette) -> None:
        self.p = p
        for b in (self.play_btn, self.prev_btn, self.next_btn, self.back_btn, self.fwd_btn, self.capture_btn):
            b.setIcon(icons.icon(b.property("icon_name"), p.text, p.border))
        self._vol_icon.setPixmap(icons.pixmap("speaker", p.muted, 16, self.devicePixelRatioF()))
        self._sync_play_icon()

    # ── 제어 ───────────────────────────────────────────────────────────────
    def load(self, path: Path | None, duration: float = 0.0, fps: float = 30.0) -> None:
        self.player.stop()
        self.duration, self.fps = duration, fps or 30.0
        self._pending_seek = self._target = None
        self.player.setSource(QUrl.fromLocalFile(str(path)) if path else QUrl())
        self._on_position(0.0)

    def _on_status(self, status) -> None:
        # 불러오기가 끝나면 재생하지 않고 0초 위치로 이동해 첫 프레임을 보여 준다
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self.player.pause()
            self.player.setPosition(0)

    def toggle_play(self) -> None:
        self._flush_seek()
        self._target = None
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def seek(self, t: float) -> None:
        """드래그 중 잦은 요청은 묶어서 처리한다."""
        self._pending_seek = max(0.0, min(self.duration, t))
        self._on_position(self._pending_seek)
        self._seek_timer.start()

    def _flush_seek(self) -> None:
        if self._pending_seek is not None:
            self._target = self._pending_seek
            self.player.setPosition(int(self._pending_seek * 1000))
            self._pending_seek = None
            self._target_timer.start()

    def _clear_target(self) -> None:
        self._target = None

    def step(self, frames: int) -> None:
        self.player.pause()
        self.seek(self.position() + frames / self.fps)

    def jump(self, seconds: float) -> None:
        self.seek(self.position() + seconds)

    def position(self) -> float:
        if self._pending_seek is not None:
            return self._pending_seek
        if self._target is not None:  # 비동기 이동이 아직 반영되지 않았을 수 있다
            return self._target
        return self.player.position() / 1000

    def _on_position(self, t: float) -> None:
        self.time_label.setText(f"{format_time(t)} / {format_time(self.duration)}")
        self.position_changed.emit(t)

    def _sync_play_icon(self) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(icons.icon("pause" if playing else "play", self.p.accent, self.p.border))
