"""최근 녹화 목록: 썸네일 · 이름 · 길이 · 해상도 · 크기."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu, QMessageBox, QWidget

from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.platform.windows.trash import TrashError, send_to_trash
from vcam.services.media_library import LibraryItem, list_recordings, load_item
from vcam.ui import icons
from vcam.ui.tokens import Palette
from vcam.util.paths import format_bytes, format_duration

log = logging.getLogger(__name__)

THUMB = QSize(112, 63)


class _Loader(QObject):
    loaded = Signal(int, object)  # generation, list[LibraryItem]


def reveal_in_explorer(path: Path) -> None:
    subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603,S607


class RecentRecordings(QListWidget):
    count_changed = Signal(int)

    def __init__(self, palette_tokens: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self.setIconSize(THUMB)
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

    def set_palette(self, p: Palette) -> None:
        self.p = p
        self._placeholder = self._make_placeholder()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(self.p.muted))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                             "아직 녹화한 영상이 없습니다. F9를 눌러 첫 녹화를 시작해 보세요.")  # fmt: skip

    def _make_placeholder(self) -> QIcon:
        return QIcon(icons.pixmap("film", self.p.muted, 40, 2.0))

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
            row.setIcon(QIcon(QPixmap(str(it.thumbnail))) if it.thumbnail else self._placeholder)
            row.setSizeHint(QSize(0, THUMB.height() + 12))
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
