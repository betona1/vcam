"""최근 녹화 목록: 썸네일 · 이름 · 길이 · 해상도 · 크기. 메인 창에 붙는 목록과 큰 썸네일의 '전체 목록' 창."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.platform.windows.trash import TrashError, send_to_trash
from vcam.services.media_library import AUDIO_SUFFIXES, LibraryItem, list_recordings, load_item
from vcam.ui import icons
from vcam.ui.tokens import Palette
from vcam.util.paths import format_bytes, format_duration

log = logging.getLogger(__name__)

THUMB = QSize(128, 72)
LARGE_THUMB = QSize(224, 126)


class _Loader(QObject):
    loaded = Signal(int, object)  # generation, list[LibraryItem]


def reveal_in_explorer(path: Path) -> None:
    subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603,S607


class RecentRecordings(QListWidget):
    count_changed = Signal(int)
    edit_requested = Signal(object)  # Path
    subtitles_requested = Signal(object)  # Path
    trashed = Signal(object)  # Path — 사용자가 휴지통으로 옮긴 파일

    def __init__(self, palette_tokens: Palette, parent: QWidget | None = None, thumb: QSize = THUMB) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self.thumb = thumb
        self.setIconSize(thumb)
        self.setSpacing(2)
        self.setUniformItemSizes(True)
        self.setAccessibleName("최근 녹화 목록")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.itemActivated.connect(lambda item: self.play(item))
        self._loader = _Loader(self)
        self._loader.loaded.connect(self._on_loaded)
        self._generation = 0
        self._placeholder = self._make_placeholder()
        self._audio_placeholder = self._make_placeholder("music")

    def set_palette(self, p: Palette) -> None:
        self.p = p
        self._placeholder = self._make_placeholder()
        self._audio_placeholder = self._make_placeholder("music")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(self.p.muted))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                             "아직 파일이 없습니다. F9로 녹화하거나 ⬇ 내려받기로 인터넷 영상을 받아 보세요.")  # fmt: skip

    def _make_placeholder(self, name: str = "film") -> QIcon:
        return QIcon(icons.pixmap(name, self.p.muted, 40, 2.0))

    def refresh(self, directory: Path, ffmpeg: FfmpegPaths | None) -> None:
        self._generation += 1
        generation = self._generation

        def work() -> None:
            try:
                items = [load_item(ffmpeg, path) for path in list_recordings(directory)]
            except OSError:
                log.warning("최근 녹화 목록을 읽지 못했습니다", exc_info=True)
                items = []
            self._loader.loaded.emit(generation, items)

        threading.Thread(target=work, name="vcam-library", daemon=True).start()

    @Slot(int, object)
    def _on_loaded(self, generation: int, items: list[LibraryItem]) -> None:
        if generation != self._generation:
            return
        self.clear()
        for it in items:
            if it.media:
                detail = f"{format_duration(it.media.duration_s)}  ·  {it.media.width}×{it.media.height}  ·  {format_bytes(it.media.size_bytes)}"
            else:
                detail = format_bytes(it.path.stat().st_size) if it.path.exists() else ""
            row = QListWidgetItem(f"{it.path.name}\n{detail}")
            row.setData(Qt.ItemDataRole.UserRole, str(it.path))
            row.setToolTip(str(it.path))
            fallback = self._audio_placeholder if it.path.suffix.lower() in AUDIO_SUFFIXES else self._placeholder
            row.setIcon(QIcon(QPixmap(str(it.thumbnail))) if it.thumbnail else fallback)
            row.setSizeHint(QSize(0, self.thumb.height() + 12))
            self.addItem(row)
        self.count_changed.emit(len(items))

    def selected_path(self) -> Path | None:
        item = self.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def play(self, item: QListWidgetItem | None = None) -> None:
        item = item or self.currentItem()
        if item:
            os.startfile(item.data(Qt.ItemDataRole.UserRole))  # noqa: S606

    def _context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction(icons.icon("play", self.p.text), "재생", lambda: self.play(item))
        menu.addAction(icons.icon("folder", self.p.text), "폴더에서 보기", lambda: reveal_in_explorer(path))
        menu.addAction(icons.icon("scissors", self.p.text), "편집기에서 열기 (자르기·변환…)", lambda: self.edit_requested.emit(path))
        menu.addAction(icons.icon("subtitles", self.p.text), "자막 만들기 (SRT)…", lambda: self.subtitles_requested.emit(path))
        menu.addSeparator()
        menu.addAction(icons.icon("trash", self.p.rec), "휴지통으로 이동…", lambda: self.trash(item))
        menu.exec(self.viewport().mapToGlobal(pos))

    def trash(self, item: QListWidgetItem | None = None) -> None:
        item = item or self.currentItem()
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(
            self, "휴지통으로 이동", f"'{path.name}' 파일을 휴지통으로 옮길까요?\n휴지통에서 다시 복원할 수 있습니다."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            send_to_trash(path)
        except TrashError as exc:
            QMessageBox.warning(self, "휴지통으로 이동 실패", str(exc))
            return
        self.takeItem(self.row(item))
        self.count_changed.emit(self.count())
        self.trashed.emit(path)


class RecordingsLibraryDialog(QDialog):
    """작업 폴더의 모든 파일을 큰 썸네일로 보여 주는 창. 메인 창 목록과 같은 동작(재생·편집·자막·휴지통)을 쓴다."""

    edit_requested = Signal(object)  # Path
    subtitles_requested = Signal(object)  # Path
    trashed = Signal(object)  # Path

    def __init__(self, palette_tokens: Palette, directory: Path, ffmpeg: FfmpegPaths | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("녹화 목록 — vCAM")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.p = palette_tokens
        self._directory, self._ffmpeg = directory, ffmpeg
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)
        head = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setProperty("role", "muted")
        self.count_label = QLabel("0")
        self.count_label.setProperty("role", "badge")
        head.addWidget(self.count_label)
        head.addWidget(self.path_label, 1)
        self.list = RecentRecordings(palette_tokens, self, thumb=LARGE_THUMB)
        self.list.setAccessibleName("전체 녹화 목록")
        self.list.count_changed.connect(lambda n: self.count_label.setText(str(n)))
        self.list.trashed.connect(self.trashed)
        self.list.edit_requested.connect(self.edit_requested)
        self.list.subtitles_requested.connect(self.subtitles_requested)
        self._buttons: list[tuple[QToolButton, str, str]] = []
        for name, tip, slot in (
            ("play", "재생", lambda: self.list.play()),
            ("scissors", "편집기에서 열기", lambda: self._emit_selected(self.edit_requested)),
            ("subtitles", "자막(SRT) 만들기", lambda: self._emit_selected(self.subtitles_requested)),
            ("folder", "폴더에서 보기", self._reveal),
            ("trash", "휴지통으로 이동", lambda: self.list.trash()),
            ("refresh", "새로 고침", self.refresh),
        ):
            b = QToolButton()
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setIconSize(QSize(20, 20))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            head.addWidget(b)
            self._buttons.append((b, name, "rec" if name == "trash" else "text"))
        layout.addLayout(head)
        layout.addWidget(self.list, 1)
        self._apply_icons()
        self.set_source(directory, ffmpeg)

    def _apply_icons(self) -> None:
        for b, name, color in self._buttons:
            b.setIcon(icons.icon(name, getattr(self.p, color)))

    def set_palette(self, p: Palette) -> None:
        self.p = p
        self.list.set_palette(p)
        self._apply_icons()

    def set_source(self, directory: Path, ffmpeg: FfmpegPaths | None) -> None:
        self._directory, self._ffmpeg = directory, ffmpeg
        self.path_label.setText(str(directory))
        self.refresh()

    def refresh(self) -> None:
        self.list.refresh(self._directory, self._ffmpeg)

    def _emit_selected(self, signal) -> None:
        path = self.list.selected_path()
        if path is not None:
            signal.emit(path)

    def _reveal(self) -> None:
        path = self.list.selected_path()
        if path is not None:
            reveal_in_explorer(path)
