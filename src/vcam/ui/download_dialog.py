"""인터넷 영상 내려받기 창: 주소 입력 → 옵션 → 순차 큐 → 결과(재생·편집·자막).

무거운 일(정보 읽기·내려받기)은 DownloadQueue의 작업 스레드가 하고, 결과는 신호로 GUI 스레드에 온다.
받은 파일은 작업 폴더에 저장되어 메인 창의 최근 녹화 목록에 바로 나타난다.
"""

from __future__ import annotations

import logging
import os
import weakref
from collections.abc import Callable
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vcam.download import engine
from vcam.download.engine import (
    AUDIO_FORMATS,
    COOKIE_PRIVACY_NOTE,
    COOKIE_SOURCES,
    KINDS,
    QUALITIES,
    DownloadOptions,
)
from vcam.download.queue import (
    CANCELLED,
    DONE,
    DOWNLOADING,
    FAILED,
    PROBING,
    STATE_LABELS,
    DownloadQueue,
    QueueItem,
)
from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.services.profile_service import Settings
from vcam.ui import icons
from vcam.ui.tokens import Palette
from vcam.ui.widgets.recent_recordings import reveal_in_explorer
from vcam.util.paths import format_bytes, format_duration

log = logging.getLogger(__name__)

THUMB = QSize(96, 54)
# 체크박스로 고르는 자막 언어 → yt-dlp 언어 패턴 (en-US, zh-Hans 같은 변형도 포함)
SUBTITLE_LANGS = (("ko", "한국어", "ko.*"), ("en", "영어", "en.*"), ("ja", "일본어", "ja.*"), ("zh", "중국어", "zh.*"))
DOWNLOAD_SETTING_KEYS = (
    "download_kind", "download_quality", "download_audio_format", "download_subtitles", "download_auto_subtitles",
    "download_subtitle_langs", "download_playlist", "download_make_subtitles", "download_cookies_browser",
    "download_cookies_file",
)  # fmt: skip


class _Bridge(QObject):
    """작업 스레드 → GUI 스레드."""

    changed = Signal(int)  # 항목 id
    log = Signal(str)
    idle = Signal()


class DownloadDialog(QDialog):
    settings_changed = Signal(dict)
    files_added = Signal(object)  # list[Path] — 작업 폴더에 새 파일이 생김 (최근 녹화 새로 고침)
    subtitles_requested = Signal(object)  # list[Path] — Whisper 자막 만들기
    edit_requested = Signal(object)  # list[Path] — 편집기에서 열기

    def __init__(
        self,
        settings: Settings,
        palette_tokens: Palette,
        ffmpeg_lookup: Callable[[], FfmpegPaths | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("인터넷 영상 내려받기")
        self.setMinimumSize(760, 640)
        self.p = palette_tokens
        self._ffmpeg_lookup = ffmpeg_lookup
        self._output_dir = settings.output_dir
        self._rows: dict[int, QTreeWidgetItem] = {}
        self._handed_to_subtitles: set[int] = set()
        self._bridge = _Bridge(self)
        self._bridge.changed.connect(self._on_item_changed)
        self._bridge.log.connect(self._append_log)
        self._bridge.idle.connect(self._on_idle)
        bridge_ref = weakref.ref(self._bridge)

        def safe(signal_name: str) -> Callable[..., None]:
            # 창이 먼저 닫혀 지워졌으면 신호를 보내지 않는다 (지워진 QObject로 emit하면 프로세스가 죽는다)
            def emit(*args) -> None:
                bridge = bridge_ref()
                if bridge is not None and shiboken6.isValid(bridge):
                    getattr(bridge, signal_name).emit(*args)

            return emit

        changed, logged, idle = safe("changed"), safe("log"), safe("idle")
        self.queue = DownloadQueue(
            ffmpeg_lookup,
            on_changed=lambda item: changed(item.id),
            on_log=lambda _level, text: logged(text),
            on_idle=idle,
        )
        self._build(settings)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build(self, s: Settings) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        url_row = QHBoxLayout()
        self.edt_url = QLineEdit()
        self.edt_url.setPlaceholderText("YouTube · TikTok · Instagram 등 영상·재생목록 주소를 붙여 넣고 Enter (여러 줄 붙여 넣기 가능)")
        self.edt_url.setAccessibleName("영상 주소")
        self.edt_url.setClearButtonEnabled(True)
        self.edt_url.returnPressed.connect(self.add_from_input)
        paste = QPushButton(icons.icon("link", self.p.text), "붙여넣기")
        paste.setToolTip("클립보드의 주소를 넣고 바로 추가")
        paste.setAccessibleName("클립보드 주소 붙여넣기")
        paste.clicked.connect(self.paste_and_add)
        self.btn_add = QPushButton(icons.icon("download", self.p.accent_text), "추가")
        self.btn_add.setProperty("role", "primary")
        self.btn_add.setDefault(True)
        self.btn_add.setAccessibleName("내려받기 목록에 추가")
        self.btn_add.clicked.connect(self.add_from_input)
        url_row.addWidget(self.edt_url, 1)
        url_row.addWidget(paste)
        url_row.addWidget(self.btn_add)
        layout.addLayout(url_row)

        grp = QGroupBox("내려받기 옵션 (추가하는 항목에 적용)")
        form = QFormLayout(grp)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.cmb_kind = QComboBox()
        for key, label in KINDS:
            self.cmb_kind.addItem(label, key)
        self._select_data(self.cmb_kind, s.download_kind)
        self.cmb_kind.setAccessibleName("내려받을 형식")
        self.cmb_quality = QComboBox()
        for key, label in QUALITIES:
            self.cmb_quality.addItem(label, key)
        self._select_data(self.cmb_quality, s.download_quality)
        self.cmb_quality.setAccessibleName("화질")
        self.cmb_audio = QComboBox()
        for key, label in AUDIO_FORMATS:
            self.cmb_audio.addItem(label, key)
        self._select_data(self.cmb_audio, s.download_audio_format)
        self.cmb_audio.setAccessibleName("소리 형식")
        kind_row = QHBoxLayout()
        kind_row.addWidget(self.cmb_kind, 1)
        kind_row.addWidget(self.cmb_quality, 1)
        kind_row.addWidget(self.cmb_audio, 1)
        self.cmb_kind.currentIndexChanged.connect(self._update_kind_widgets)
        form.addRow("형식·화질", kind_row)

        sub_row = QHBoxLayout()
        self.chk_subtitles = QCheckBox("사이트 자막 받기 (SRT)")
        self.chk_subtitles.setChecked(s.download_subtitles)
        self.chk_subtitles.setToolTip("영상에 올라온 자막을 영상이름.언어.srt 로 함께 저장합니다.")
        sub_row.addWidget(self.chk_subtitles)
        self.lang_boxes: dict[str, QCheckBox] = {}
        for code, label, _pattern in SUBTITLE_LANGS:
            box = QCheckBox(label)
            box.setChecked(code in s.download_subtitle_langs)
            self.lang_boxes[code] = box
            sub_row.addWidget(box)
        self.chk_auto_subs = QCheckBox("자동 생성 자막도")
        self.chk_auto_subs.setChecked(s.download_auto_subtitles)
        self.chk_auto_subs.setToolTip("사이트가 음성 인식으로 만든 자막도 받습니다 (정확도가 낮을 수 있음).")
        sub_row.addWidget(self.chk_auto_subs)
        sub_row.addStretch(1)
        self.chk_subtitles.toggled.connect(self._update_subtitle_widgets)
        form.addRow("자막", sub_row)

        self.chk_playlist = QCheckBox("재생목록 주소면 전체 받기 (파일 이름 앞에 순번)")
        self.chk_playlist.setChecked(s.download_playlist)
        form.addRow("", self.chk_playlist)
        self.chk_make_subtitles = QCheckBox("내려받은 뒤 PC 안의 Whisper 음성 인식으로 자막 만들기 (자막 만들기 창이 열립니다)")
        self.chk_make_subtitles.setChecked(s.download_make_subtitles)
        form.addRow("", self.chk_make_subtitles)

        cookie_row = QHBoxLayout()
        self.cmb_cookies = QComboBox()
        for key, label in COOKIE_SOURCES:
            self.cmb_cookies.addItem(label, key)
        self._select_data(self.cmb_cookies, s.download_cookies_browser)
        self.cmb_cookies.setAccessibleName("로그인 쿠키를 가져올 곳")
        self.cmb_cookies.setToolTip(COOKIE_PRIVACY_NOTE)
        self.edt_cookie_file = QLineEdit(s.download_cookies_file)
        self.edt_cookie_file.setPlaceholderText("cookies.txt 파일 (Netscape 형식 — 브라우저 확장으로 내보낸 파일)")
        self.edt_cookie_file.setAccessibleName("cookies.txt 파일 경로")
        self.btn_cookie_file = QPushButton(icons.icon("folder", self.p.text), "찾기…")
        self.btn_cookie_file.setAccessibleName("cookies.txt 파일 찾기")
        self.btn_cookie_file.clicked.connect(self._pick_cookie_file)
        cookie_row.addWidget(self.cmb_cookies)
        cookie_row.addWidget(self.edt_cookie_file, 1)
        cookie_row.addWidget(self.btn_cookie_file)
        self.cmb_cookies.currentIndexChanged.connect(self._update_cookie_widgets)
        form.addRow("로그인 쿠키", cookie_row)
        self.lbl_cookie_hint = QLabel(COOKIE_PRIVACY_NOTE)
        self.lbl_cookie_hint.setProperty("role", "muted")
        self.lbl_cookie_hint.setWordWrap(True)
        form.addRow("", self.lbl_cookie_hint)
        layout.addWidget(grp)

        save_row = QHBoxLayout()
        self.lbl_output = QLabel()
        self.lbl_output.setProperty("role", "muted")
        self.lbl_output.setWordWrap(True)
        open_dir = QPushButton(icons.icon("folder", self.p.text), "")
        open_dir.setToolTip("작업 폴더 열기")
        open_dir.setAccessibleName("작업 폴더 열기")
        open_dir.clicked.connect(self._open_output_dir)
        save_row.addWidget(self.lbl_output, 1)
        save_row.addWidget(open_dir)
        layout.addLayout(save_row)
        self.set_output_dir(self._output_dir)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["제목", "길이", "상태"])
        self.tree.setColumnWidth(0, 400)
        self.tree.setColumnWidth(1, 70)
        self.tree.setIconSize(THUMB)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAccessibleName("내려받기 목록")
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemActivated.connect(lambda row, _c: self._play(self._id_of(row)))
        layout.addWidget(self.tree, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("현재 항목 진행률")
        layout.addWidget(self.progress)
        self.status = QLabel("주소를 추가하면 바로 내려받기를 시작합니다.")
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFixedHeight(90)
        self.log_view.setAccessibleName("진행 기록")
        layout.addWidget(self.log_view)

        buttons = QHBoxLayout()
        self.lbl_engine = QLabel()
        self.lbl_engine.setProperty("role", "muted")
        self.lbl_engine.setWordWrap(True)
        self.lbl_engine.setText(self._engine_text())
        buttons.addWidget(self.lbl_engine, 1)
        self.btn_cancel_all = QPushButton(icons.icon("close", self.p.text), "모두 취소")
        self.btn_cancel_all.clicked.connect(self.queue.cancel_all)
        self.btn_cancel_all.setEnabled(False)
        self.btn_close = QPushButton("닫기")
        self.btn_close.clicked.connect(self.close)
        buttons.addWidget(self.btn_cancel_all)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self._update_kind_widgets()
        self._update_subtitle_widgets()
        self._update_cookie_widgets()

    def _engine_text(self) -> str:
        avail = engine.availability()
        if not avail.ok:
            return "yt-dlp가 설치되어 있지 않아 내려받을 수 없습니다."
        rt = self.queue.js_runtime
        runtime = f"JS 런타임: {rt[0]}" if rt else "JS 런타임 없음 → 일부 화질을 못 받을 수 있음 (" + engine.DENO_INSTALL_HINT + ")"
        imp = engine.impersonation_available()
        site = "브라우저 위장: 있음" if imp.ok else "브라우저 위장 없음 → TikTok·Instagram 등을 못 받을 수 있음 (" + engine.CURL_CFFI_INSTALL_HINT + ")"
        return f"yt-dlp {engine.engine_version()} · {runtime} · {site}"

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _update_kind_widgets(self) -> None:
        audio = self.cmb_kind.currentData() == "audio"
        self.cmb_quality.setVisible(not audio)
        self.cmb_audio.setVisible(audio)

    def _update_subtitle_widgets(self) -> None:
        on = self.chk_subtitles.isChecked()
        for box in self.lang_boxes.values():
            box.setEnabled(on)
        self.chk_auto_subs.setEnabled(on)

    def _update_cookie_widgets(self) -> None:
        source = self.cmb_cookies.currentData()
        self.edt_cookie_file.setVisible(source == "file")
        self.btn_cookie_file.setVisible(source == "file")
        self.lbl_cookie_hint.setVisible(bool(source))

    def _pick_cookie_file(self) -> None:
        start = self.edt_cookie_file.text().strip() or self._output_dir
        path, _ = QFileDialog.getOpenFileName(self, "cookies.txt 파일", start, "쿠키 파일 (*.txt);;모든 파일 (*)")
        if path:
            self.edt_cookie_file.setText(path)

    def set_output_dir(self, path: str) -> None:
        self._output_dir = path
        self.lbl_output.setText(f"저장: <b>{path}</b>  (작업 폴더 — 메인 화면·설정에서 바꿉니다. 받은 파일은 최근 녹화에 나타납니다)")
        self.lbl_output.setTextFormat(Qt.TextFormat.RichText)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.prefill_from_clipboard()
        self.edt_url.setFocus()

    def prefill_from_clipboard(self) -> bool:
        """주소 칸이 비어 있고 클립보드에 주소가 있으면 채워 넣는다 (자동으로 추가하지는 않음)."""
        if self.edt_url.text().strip():
            return False
        text = QGuiApplication.clipboard().text() if QGuiApplication.clipboard() else ""
        if engine.is_probable_url(text):
            self.edt_url.setText(text.strip())
            self.edt_url.selectAll()
            return True
        return False

    # ── 옵션 ──────────────────────────────────────────────────────────────
    def options(self) -> DownloadOptions:
        langs = tuple(code for code, box in self.lang_boxes.items() if box.isChecked())
        return DownloadOptions(
            kind=self.cmb_kind.currentData(),
            quality=self.cmb_quality.currentData(),
            audio_format=self.cmb_audio.currentData(),
            subtitles=self.chk_subtitles.isChecked() and bool(langs),
            auto_subtitles=self.chk_subtitles.isChecked() and self.chk_auto_subs.isChecked() and bool(langs),
            subtitle_langs=tuple(pattern for code, _l, pattern in SUBTITLE_LANGS if code in langs),
            playlist=self.chk_playlist.isChecked(),
            cookies_browser=self.cmb_cookies.currentData() or "",
            cookies_file=self.edt_cookie_file.text().strip(),
            output_dir=self._output_dir,
        )

    def _settings_changes(self) -> dict:
        return {
            "download_kind": self.cmb_kind.currentData(),
            "download_quality": self.cmb_quality.currentData(),
            "download_audio_format": self.cmb_audio.currentData(),
            "download_subtitles": self.chk_subtitles.isChecked(),
            "download_auto_subtitles": self.chk_auto_subs.isChecked(),
            "download_subtitle_langs": tuple(code for code, box in self.lang_boxes.items() if box.isChecked()),
            "download_playlist": self.chk_playlist.isChecked(),
            "download_make_subtitles": self.chk_make_subtitles.isChecked(),
            "download_cookies_browser": self.cmb_cookies.currentData() or "",
            "download_cookies_file": self.edt_cookie_file.text().strip(),
        }

    # ── 추가 ──────────────────────────────────────────────────────────────
    def paste_and_add(self) -> None:
        text = QGuiApplication.clipboard().text() if QGuiApplication.clipboard() else ""
        if not engine.extract_urls(text):
            QMessageBox.information(self, "붙여넣기", "클립보드에 영상 주소(http…)가 없습니다.")
            return
        self.edt_url.setText(text.strip())
        self.add_from_input()

    def add_from_input(self) -> None:
        self.add_urls(engine.extract_urls(self.edt_url.text()))

    def add_urls(self, urls: list[str]) -> list[QueueItem]:
        urls = [u for u in urls if u]
        if not urls:
            QMessageBox.information(self, "인터넷 영상 내려받기", "영상 주소(http…)를 넣어 주세요.")
            return []
        avail = engine.availability()
        if not avail.ok:
            QMessageBox.information(self, "인터넷 영상 내려받기", avail.reason)
            return []
        options = self.options()
        if options.kind == "video" and self._ffmpeg_lookup() is None:
            answer = QMessageBox.question(
                self, "FFmpeg 없음",
                "FFmpeg를 찾을 수 없어 고화질 영상(영상+소리 따로 제공)을 합치지 못합니다.\n"
                "낮은 화질로라도 계속할까요? (설정에서 FFmpeg 위치를 지정하면 해결됩니다)",
            )  # fmt: skip
            if answer != QMessageBox.StandardButton.Yes:
                return []
        if options.kind == "audio" and self._ffmpeg_lookup() is None:
            QMessageBox.warning(self, "FFmpeg 없음", "소리만 추출하려면 FFmpeg가 필요합니다. 설정에서 FFmpeg 위치를 지정해 주세요.")
            return []
        if options.cookies_browser == "file" and not Path(options.cookies_file or "").is_file():
            QMessageBox.warning(self, "로그인 쿠키", "cookies.txt 파일을 찾을 수 없습니다. 파일을 고르거나 '로그인 쿠키'를 '사용 안 함'으로 바꿔 주세요.")
            return []
        try:
            Path(options.output_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "작업 폴더", f"작업 폴더를 만들 수 없습니다: {exc}")
            return []
        self.settings_changed.emit(self._settings_changes())
        items = [self.queue.add(url, options) for url in urls]
        self.edt_url.clear()
        self.btn_cancel_all.setEnabled(True)
        log.info("내려받기 추가: %d개, %s, %s", len(items), options.kind, options.quality_label)
        return items

    # ── 목록 갱신 ──────────────────────────────────────────────────────────
    def _id_of(self, row: QTreeWidgetItem | None) -> int:
        return int(row.data(0, Qt.ItemDataRole.UserRole)) if row else -1

    @Slot(int)
    def _on_item_changed(self, item_id: int) -> None:
        item = self.queue.find(item_id)
        if item is None:
            return
        row = self._rows.get(item_id)
        if row is None:
            row = QTreeWidgetItem(["", "", ""])
            row.setData(0, Qt.ItemDataRole.UserRole, item_id)
            row.setIcon(0, icons.icon("film", self.p.muted))
            self.tree.addTopLevelItem(row)
            self._rows[item_id] = row
        cand = item.candidate
        if cand is not None and not row.data(0, Qt.ItemDataRole.UserRole + 1):
            row.setData(0, Qt.ItemDataRole.UserRole + 1, True)
            pm = QPixmap()
            if cand.thumbnail and pm.loadFromData(cand.thumbnail):
                row.setIcon(0, QIcon(pm.scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        if cand is not None:
            # 길이는 받은 뒤에 채워질 수 있다 (Instagram처럼 미리 안 주는 사이트)
            row.setText(1, f"{cand.entry_count}개" if cand.is_playlist else (format_duration(cand.duration_s) if cand.duration_s else ""))
        title = item.title
        if cand is not None and cand.uploader:
            title += f"\n{cand.uploader}"
        row.setText(0, title)
        row.setToolTip(0, item.url if not item.files else "\n".join(str(p) for p in item.files))
        row.setText(2, self._status_text(item))
        if item.state == DONE:
            row.setIcon(2, icons.icon("check", self.p.ok))
        elif item.state == FAILED:
            row.setIcon(2, icons.icon("alert", self.p.rec))
        elif item.state == CANCELLED:
            row.setIcon(2, icons.icon("close", self.p.muted))
        else:
            row.setIcon(2, QIcon())
        self._refresh_overall(item)
        if item.state == DONE and item.files and not row.data(0, Qt.ItemDataRole.UserRole + 2):
            row.setData(0, Qt.ItemDataRole.UserRole + 2, True)
            self.files_added.emit(list(item.files))
            self._append_log(f"✔ {item.title[:60]} → {', '.join(p.name for p in item.files)}")

    @staticmethod
    def _status_text(item: QueueItem) -> str:
        if item.state == DOWNLOADING and item.progress is not None:
            p = item.progress
            prefix = f"[{p.index}/{p.count}] " if p.count else ""
            if p.stage == "download":
                parts = [f"{int(p.fraction * 100)}%"]
                if p.total:
                    parts.append(f"{format_bytes(p.downloaded)} / {format_bytes(p.total)}")
                if p.speed:
                    parts.append(f"{format_bytes(int(p.speed))}/s")
                if p.eta_s >= 0:
                    parts.append(f"남은 {format_duration(p.eta_s)}")
                return prefix + " · ".join(parts)
            return prefix + engine.STAGE_LABELS.get(p.stage, p.stage)
        if item.state == FAILED:
            return f"실패: {item.error}"
        if item.state == DONE and item.result:
            extra = f" · 자막 {len(item.result.subtitles)}개" if item.result.subtitles else ""
            return "완료" + extra
        return STATE_LABELS.get(item.state, item.state)

    def _refresh_overall(self, item: QueueItem) -> None:
        if item.state in (PROBING, DOWNLOADING):
            frac = item.progress.fraction if item.progress and item.progress.stage == "download" else 0.0
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(frac * 1000))
            self.status.setText(f"{item.title[:70]} — {self._status_text(item)}")
        elif not self.queue.is_busy:
            items = self.queue.items
            done = sum(1 for i in items if i.state == DONE)
            failed = sum(1 for i in items if i.state == FAILED)
            cancelled = sum(1 for i in items if i.state == CANCELLED)
            self.progress.setValue(1000 if done and not failed else 0)
            text = f"완료 {done}개"
            if failed:
                text += f" · 실패 {failed}개"
            if cancelled:
                text += f" · 취소 {cancelled}개"
            self.status.setText(text + ". 받은 파일은 최근 녹화 목록에 있습니다.")
            self.btn_cancel_all.setEnabled(False)

    @Slot()
    def _on_idle(self) -> None:
        self.btn_cancel_all.setEnabled(False)
        if not self.chk_make_subtitles.isChecked():
            return
        files = []
        for item in self.queue.items:
            if item.state == DONE and item.id not in self._handed_to_subtitles:
                self._handed_to_subtitles.add(item.id)
                files.extend(p for p in item.files if p.suffix.lower() not in (".srt",))
        if files:
            self.subtitles_requested.emit(files)

    @Slot(str)
    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ── 항목 동작 ──────────────────────────────────────────────────────────
    def _context_menu(self, pos) -> None:
        row = self.tree.itemAt(pos)
        if row is None:
            return
        item_id = self._id_of(row)
        item = self.queue.find(item_id)
        if item is None:
            return
        menu = QMenu(self)
        if item.files:
            menu.addAction(icons.icon("play", self.p.text), "재생", lambda: self._play(item_id))
            menu.addAction(icons.icon("folder", self.p.text), "폴더에서 보기", lambda: reveal_in_explorer(item.files[0]))
            menu.addAction(icons.icon("scissors", self.p.text), "편집기에서 열기 (자르기·변환…)", lambda: self.edit_requested.emit(list(item.files)))
            menu.addAction(icons.icon("subtitles", self.p.text), "자막 만들기 (Whisper)…", lambda: self.subtitles_requested.emit(list(item.files)))
            menu.addSeparator()
        if item.is_active:
            menu.addAction(icons.icon("close", self.p.rec), "취소", lambda: self.queue.cancel(item_id))
        else:
            menu.addAction(icons.icon("refresh", self.p.text), "다시 받기", lambda: self._retry(item_id))
            menu.addAction(icons.icon("minus", self.p.text), "목록에서 제거", lambda: self._remove(item_id))
        menu.addAction(icons.icon("link", self.p.text), "주소 복사", lambda: QGuiApplication.clipboard().setText(item.url))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _play(self, item_id: int) -> None:
        item = self.queue.find(item_id)
        if item and item.files and item.files[0].exists():
            os.startfile(str(item.files[0]))  # noqa: S606

    def _retry(self, item_id: int) -> None:
        item = self.queue.find(item_id)
        if item is None or item.is_active:
            return
        self._remove(item_id)
        self.queue.add(item.url, item.options)
        self.btn_cancel_all.setEnabled(True)

    def _remove(self, item_id: int) -> None:
        if not self.queue.remove(item_id):
            return
        row = self._rows.pop(item_id, None)
        if row is not None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(row))

    def _open_output_dir(self) -> None:
        path = Path(self._output_dir)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # noqa: S606

    # ── 닫기 ──────────────────────────────────────────────────────────────
    @property
    def is_busy(self) -> bool:
        return self.queue.is_busy

    def cancel(self) -> None:
        self.queue.cancel_all()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.is_busy:
            answer = QMessageBox.question(self, "인터넷 영상 내려받기", "내려받는 중입니다. 취소하고 닫을까요?\n(받다 만 조각 파일은 다시 받을 때 이어서 씁니다)")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.queue.cancel_all()
        self.queue.shutdown(timeout=5.0)
        super().closeEvent(event)
