"""vCAM 동영상 편집기: 자르기 · 구간 제거 · 나누기 · 합치기 · 소리 추출 · 소리 제거 · 변환.

편집 엔진(vcam.editing)만 호출하며, 파일 분석·인코딩은 모두 작업 스레드에서 수행한다.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vcam.editing.formats import (
    AUDIO_FORMATS,
    EncodeSettings,
    available_audio_codecs,
    available_video_codecs,
)
from vcam.editing.jobs import (
    Cancelled,
    EditError,
    EditJob,
    Piece,
    capture_frame,
    execute,
    load_media,
)
from vcam.editing.probe import MediaFile
from vcam.editing.segments import (
    Segment,
    complement,
    format_time,
    normalize,
    split_at,
    split_equal,
    split_every,
)
from vcam.editing.storage import PROJECT_EXT, ProjectFile
from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.ui import icons
from vcam.ui.editor.encode_dialog import EncodeDialog, summarize
from vcam.ui.editor.player import Player
from vcam.ui.editor.timeline import Timeline
from vcam.ui.tokens import Palette
from vcam.ui.widgets.recent_recordings import reveal_in_explorer

log = logging.getLogger(__name__)

MEDIA_FILTER = (
    "동영상·소리 파일 (*.mp4 *.mkv *.webm *.avi *.mov *.wmv *.flv *.m4v *.ts *.mts *.m2ts *.mpg *.mpeg *.vob "
    "*.3gp *.mp3 *.m4a *.wav *.flac *.ogg *.opus *.wma *.aac);;모든 파일 (*.*)"
)
MEDIA_EXTS = {e.strip("*") for e in MEDIA_FILTER.split("(")[1].split(")")[0].split()}


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    icon: str
    hint: str
    suffix: str


TOOLS = [
    Tool("cut", "자르기", "scissors", "남길 구간을 표시하면 그 부분만 저장합니다. 여러 구간을 표시할 수 있습니다.", "_자르기"),
    Tool("remove", "구간 제거", "erase", "지울 구간(광고·실수한 부분 등)을 표시하면 그 부분을 빼고 이어 붙입니다.", "_구간제거"),
    Tool("split", "나누기", "split", "한 동영상을 여러 파일로 나눕니다. 직접 지점을 찍거나 균등·시간 단위로 나눌 수 있습니다.", "_나누기"),
    Tool("merge", "합치기", "merge", "왼쪽 목록의 파일을 위에서부터 순서대로 이어 붙입니다.", "_합치기"),
    Tool("extract", "소리 추출", "music", "소리만 MP3·M4A·WAV 등으로 저장합니다. 구간을 표시하면 그 부분만 저장합니다.", ""),
    Tool("mute", "소리 제거", "speaker_off", "소리를 빼고 영상만 저장합니다(화질 손실 없음).", "_소리제거"),
    Tool("convert", "변환", "convert", "MP4·MKV·WebM·AVI·MOV·WMV·GIF 등 다른 형식·코덱·크기로 바꿉니다.", "_변환"),
]
TOOL_BY_KEY = {t.key: t for t in TOOLS}


class _Bridge(QObject):
    loaded = Signal(object, object)  # MediaFile | None, 오류 문자열 | None
    progress = Signal(float, str)
    done = Signal(object, object)  # 결과 목록, 오류
    captured = Signal(object)


class EditorWindow(QMainWindow):
    def __init__(self, palette_tokens: Palette, ffmpeg_lookup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.p = palette_tokens
        self._ffmpeg_lookup = ffmpeg_lookup  # () -> FfmpegPaths | None
        self.files: list[MediaFile] = []
        self.current: MediaFile | None = None
        self.segments: dict[Path, list[Segment]] = {}
        self.split_points: dict[Path, list[float]] = {}
        self.sel_in: float | None = None
        self.sel_out: float | None = None
        self.tool = "cut"
        self.encode = EncodeSettings()
        self._cancel: threading.Event | None = None
        self._outputs: list[Path] = []
        self._themed: list[tuple[object, str, str]] = []

        self.setWindowTitle("vCAM 편집기")
        self.resize(1320, 820)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)
        self._bridge = _Bridge(self)
        self._bridge.loaded.connect(self._on_loaded)
        self._bridge.progress.connect(self._on_progress)
        self._bridge.done.connect(self._on_done)
        self._bridge.captured.connect(self._on_captured)
        self._build()
        self._shortcuts()
        self._apply_icons()
        self._select_tool("cut")

    # ── 구성 ───────────────────────────────────────────────────────────────
    def _icon_btn(self, name: str, tip: str, slot, text: str = "", color: str = "text") -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setIconSize(QSize(18, 18))
        if text:
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.clicked.connect(slot)
        self._themed.append((b, name, color))
        return b

    def _build(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        # 상단: 파일·프로젝트 + 작업 종류
        top = QHBoxLayout()
        top.addWidget(self._icon_btn("open", "동영상 열기 (Ctrl+O)", self.open_dialog, "열기"))
        top.addWidget(self._icon_btn("folder", "프로젝트 열기", self.open_project, "프로젝트 열기"))
        top.addWidget(self._icon_btn("save", "프로젝트 저장 (Ctrl+S)", self.save_project, "프로젝트 저장"))
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        top.addWidget(sep)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons: dict[str, QToolButton] = {}
        for t in TOOLS:
            b = QToolButton()
            b.setText(t.label)
            b.setToolTip(t.hint)
            b.setCheckable(True)
            b.setProperty("role", "mode")
            b.setIconSize(QSize(20, 20))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.clicked.connect(lambda _c=False, k=t.key: self._select_tool(k))
            self.tool_group.addButton(b)
            self._themed.append((b, t.icon, "mode"))
            self.tool_buttons[t.key] = b
            top.addWidget(b)
        top.addStretch(1)
        outer.addLayout(top)
        self.hint = QLabel()
        self.hint.setProperty("role", "muted")
        outer.addWidget(self.hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_files_panel())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_output_panel())
        splitter.setSizes([240, 760, 320])
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)
        outer.addWidget(self._build_bottom())
        self.setCentralWidget(root)

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        if title:
            label = QLabel(title)
            label.setProperty("role", "section")
            layout.addWidget(label)
        return frame, layout

    def _build_files_panel(self) -> QWidget:
        frame, layout = self._card("")
        head = QHBoxLayout()
        title = QLabel("파일")
        title.setProperty("role", "section")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._icon_btn("plus", "파일 추가", self.open_dialog))
        head.addWidget(self._icon_btn("minus", "선택한 파일 빼기", self.remove_current))
        head.addWidget(self._icon_btn("up", "위로", lambda: self._move_file(-1)))
        head.addWidget(self._icon_btn("down", "아래로", lambda: self._move_file(1)))
        layout.addLayout(head)
        self.file_list = QListWidget()
        self.file_list.setAccessibleName("편집할 파일 목록")
        self.file_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.currentRowChanged.connect(self._on_file_row)
        layout.addWidget(self.file_list, 1)
        drop = QLabel("파일을 여기로 끌어다 놓아도 됩니다")
        drop.setProperty("role", "muted")
        drop.setWordWrap(True)
        layout.addWidget(drop)
        self.file_info = QLabel("")
        self.file_info.setProperty("role", "muted")
        self.file_info.setWordWrap(True)
        layout.addWidget(self.file_info)
        return frame

    def _build_center(self) -> QWidget:
        frame, layout = self._card("")
        self.player = Player(self.p)
        self.player.position_changed.connect(self._on_position)
        self.player.capture_requested.connect(self.capture)
        layout.addWidget(self.player, 1)
        self.timeline = Timeline(self.p)
        self.timeline.seek.connect(self.player.seek)
        self.timeline.selection_changed.connect(self._on_timeline_selection)
        layout.addWidget(self.timeline)

        mark = QHBoxLayout()
        mark.addWidget(self._icon_btn("mark_in", "현재 위치를 시작점으로 ( [ )", self.mark_in, "시작점"))
        self.in_label = QLabel("--:--.---")
        mark.addWidget(self.in_label)
        mark.addSpacing(6)
        mark.addWidget(self._icon_btn("mark_out", "현재 위치를 끝점으로 ( ] )", self.mark_out, "끝점"))
        self.out_label = QLabel("--:--.---")
        mark.addWidget(self.out_label)
        mark.addSpacing(10)
        self.add_seg_btn = QPushButton("구간 추가 (Enter)")
        self.add_seg_btn.setProperty("role", "primary")
        self.add_seg_btn.clicked.connect(self.add_segment)
        mark.addWidget(self.add_seg_btn)
        mark.addStretch(1)
        self.sel_len = QLabel("")
        self.sel_len.setProperty("role", "muted")
        mark.addWidget(self.sel_len)
        layout.addLayout(mark)

        self.stack = QStackedWidget()
        # 구간 목록
        seg_page = QWidget()
        sl = QVBoxLayout(seg_page)
        sl.setContentsMargins(0, 0, 0, 0)
        shead = QHBoxLayout()
        self.seg_title = QLabel("구간")
        self.seg_title.setProperty("role", "section")
        shead.addWidget(self.seg_title)
        shead.addStretch(1)
        shead.addWidget(self._icon_btn("trash", "선택한 구간 지우기 (Delete)", self.delete_segment, "지우기"))
        shead.addWidget(self._icon_btn("close", "구간 모두 지우기", self.clear_segments, "모두 지우기"))
        sl.addLayout(shead)
        self.seg_tree = QTreeWidget()
        self.seg_tree.setHeaderLabels(["#", "시작", "끝", "길이"])
        self.seg_tree.setRootIsDecorated(False)
        self.seg_tree.setMaximumHeight(150)
        self.seg_tree.itemDoubleClicked.connect(lambda item, _c: self.player.seek(item.data(1, Qt.ItemDataRole.UserRole)))
        sl.addWidget(self.seg_tree)
        self.stack.addWidget(seg_page)
        # 나누기 설정
        split_page = QWidget()
        spl = QVBoxLayout(split_page)
        spl.setContentsMargins(0, 0, 0, 0)
        self.split_manual = QRadioButton("직접 지점 지정")
        self.split_equal = QRadioButton("같은 길이로")
        self.split_every = QRadioButton("일정 시간마다")
        self.split_manual.setChecked(True)
        self.split_count = QSpinBox()
        self.split_count.setRange(2, 999)
        self.split_count.setValue(2)
        self.split_count.setSuffix(" 개 파일")
        self.split_secs = QDoubleSpinBox()
        self.split_secs.setRange(1, 36000)
        self.split_secs.setValue(60)
        self.split_secs.setSuffix(" 초마다")
        add_point = QPushButton("현재 위치에서 나누기 (S)")
        add_point.clicked.connect(self.add_split_point)
        clear_points = QPushButton("지점 모두 지우기")
        clear_points.clicked.connect(self.clear_split_points)
        r1 = QHBoxLayout()
        r1.addWidget(self.split_manual)
        r1.addWidget(add_point)
        r1.addWidget(clear_points)
        r1.addStretch(1)
        r2 = QHBoxLayout()
        r2.addWidget(self.split_equal)
        r2.addWidget(self.split_count)
        r2.addSpacing(16)
        r2.addWidget(self.split_every)
        r2.addWidget(self.split_secs)
        r2.addStretch(1)
        for w in (self.split_manual, self.split_equal, self.split_every):
            w.toggled.connect(self._refresh_view)
        self.split_count.valueChanged.connect(self._refresh_view)
        self.split_secs.valueChanged.connect(self._refresh_view)
        self.split_summary = QLabel("")
        self.split_summary.setProperty("role", "muted")
        spl.addLayout(r1)
        spl.addLayout(r2)
        spl.addWidget(self.split_summary)
        spl.addStretch(1)
        self.stack.addWidget(split_page)
        layout.addWidget(self.stack)
        return frame

    def _build_output_panel(self) -> QWidget:
        frame, layout = self._card("출력")
        # 처리 방식
        self.mode_box = QGroupBox("처리 방식")
        ml = QVBoxLayout(self.mode_box)
        self.mode_fast = QRadioButton("빠른 모드 (무손실)")
        self.mode_fast.setToolTip("다시 인코딩하지 않아 화질 손실이 없고 매우 빠릅니다. 자르는 지점은 키프레임에 맞춰집니다.")
        self.mode_encode = QRadioButton("인코딩 모드")
        self.mode_encode.setToolTip("프레임 단위로 정확히 자르고, 형식·코덱·크기·속도를 바꿀 수 있습니다.")
        self.mode_fast.setChecked(True)
        self.mode_fast.toggled.connect(self._refresh_output_controls)
        ml.addWidget(self.mode_fast)
        ml.addWidget(self.mode_encode)
        self.mode_hint = QLabel("")
        self.mode_hint.setProperty("role", "muted")
        self.mode_hint.setWordWrap(True)
        ml.addWidget(self.mode_hint)
        layout.addWidget(self.mode_box)
        # 인코딩 설정
        self.enc_box = QGroupBox("인코딩 설정")
        el = QVBoxLayout(self.enc_box)
        self.enc_summary = QLabel(summarize(self.encode))
        self.enc_summary.setWordWrap(True)
        enc_btn = self._icon_btn("tune", "형식·코덱·크기·화질·속도 설정", self.open_encode_settings, "설정…")
        el.addWidget(self.enc_summary)
        el.addWidget(enc_btn, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.enc_box)
        # 저장 옵션
        opt = QGroupBox("저장 옵션")
        ol = QVBoxLayout(opt)
        self.opt_merge = QCheckBox("구간을 하나의 파일로 합치기")
        self.opt_merge.setChecked(True)
        self.opt_merge.setToolTip("끄면 구간마다 따로 저장합니다")
        self.opt_extract = QCheckBox("소리 따로 저장")
        self.audio_fmt = QComboBox()
        for key, (label, _ext, _codec) in AUDIO_FORMATS.items():
            self.audio_fmt.addItem(label, key)
        af = QHBoxLayout()
        af.addWidget(self.opt_extract)
        af.addWidget(self.audio_fmt, 1)
        self.opt_mute = QCheckBox("영상에서 소리 제거")
        self.opt_stamp = QCheckBox("타임스탬프 정보 저장 (.txt)")
        self.opt_all = QCheckBox("목록의 모든 파일에 적용 (일괄 처리)")
        for w in (self.opt_merge, self.opt_mute, self.opt_stamp, self.opt_all):
            ol.addWidget(w)
        ol.insertLayout(1, af)
        layout.addWidget(opt)
        # 저장 위치·이름
        loc = QGroupBox("저장 위치")
        ll = QVBoxLayout(loc)
        self.same_folder = QCheckBox("원본과 같은 폴더")
        self.same_folder.setChecked(True)
        self.out_dir = QLineEdit(str(Path.home() / "Videos" / "vcam"))
        browse = self._icon_btn("folder", "저장 폴더 선택", self._browse_out)
        drow = QHBoxLayout()
        drow.addWidget(self.out_dir, 1)
        drow.addWidget(browse)
        self.same_folder.toggled.connect(lambda on: (self.out_dir.setEnabled(not on), browse.setEnabled(not on)))
        self.out_dir.setEnabled(False)
        browse.setEnabled(False)
        self.out_name = QLineEdit()
        self.out_name.setPlaceholderText("파일 이름 (비우면 원본 이름 사용)")
        ll.addWidget(self.same_folder)
        ll.addLayout(drow)
        ll.addWidget(self.out_name)
        layout.addWidget(loc)
        layout.addStretch(1)
        return frame

    def _build_bottom(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("card")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 12, 8)
        self.status = QLabel("동영상을 열어 편집을 시작하세요. (Ctrl+O 또는 끌어다 놓기)")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        self.progress.hide()
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.cancel_btn.hide()
        self.open_result = self._icon_btn("play", "결과 파일 열기", self._open_result, "결과 보기")
        self.open_result_dir = self._icon_btn("folder", "결과 폴더 열기", self._reveal_result)
        self.open_result.hide()
        self.open_result_dir.hide()
        self.start_btn = QPushButton("시작")
        self.start_btn.setObjectName("recordButton")
        self.start_btn.setIconSize(QSize(18, 18))
        self.start_btn.clicked.connect(self.start)
        bl.addWidget(self.status, 1)
        bl.addWidget(self.open_result)
        bl.addWidget(self.open_result_dir)
        bl.addWidget(self.progress)
        bl.addWidget(self.cancel_btn)
        bl.addWidget(self.start_btn)
        return bar

    def _shortcuts(self) -> None:
        for keys, slot in (
            ("Space", self.player.toggle_play), ("Left", lambda: self.player.step(-1)), ("Right", lambda: self.player.step(1)),
            ("Shift+Left", lambda: self.player.jump(-5)), ("Shift+Right", lambda: self.player.jump(5)),
            ("[", self.mark_in), ("]", self.mark_out), ("I", self.mark_in), ("O", self.mark_out),
            ("Return", self.add_segment), ("Enter", self.add_segment), ("Delete", self.delete_segment),
            ("S", self.add_split_point), ("Home", lambda: self.player.seek(0)),
            ("End", lambda: self.player.seek(self.current.duration if self.current else 0)),
            (QKeySequence.StandardKey.Open, self.open_dialog), (QKeySequence.StandardKey.Save, self.save_project),
        ):  # fmt: skip
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

    def _apply_icons(self) -> None:
        p = self.p
        for target, name, color in self._themed:
            if color == "mode":
                target.setIcon(icons.icon(name, p.muted, p.border, p.accent))
            else:
                target.setIcon(icons.icon(name, getattr(p, color), p.border))
        self.start_btn.setIcon(icons.icon("play", "#ffffff"))

    def set_palette(self, p: Palette) -> None:
        self.p = p
        self.player.apply_palette(p)
        self.timeline.set_palette(p)
        self._apply_icons()

    # ── 파일 ───────────────────────────────────────────────────────────────
    def ffmpeg(self) -> FfmpegPaths | None:
        ff = self._ffmpeg_lookup()
        if ff is None:
            QMessageBox.warning(self, "vCAM 편집기", "FFmpeg를 찾을 수 없습니다. vCAM 설정에서 FFmpeg 위치를 지정해 주세요.")
        return ff

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "편집할 파일 열기", "", MEDIA_FILTER)
        self.open_files([Path(p) for p in paths])

    def open_files(self, paths: list[Path]) -> None:
        ff = self.ffmpeg() if paths else None
        if ff is None:
            return
        self.status.setText("파일을 분석하는 중…")

        def work() -> None:
            for path in paths:
                try:
                    self._bridge.loaded.emit(load_media(ff, path), None)
                except EditError as exc:
                    self._bridge.loaded.emit(None, f"{path.name}: {exc}")

        threading.Thread(target=work, name="vcam-edit-probe", daemon=True).start()

    @Slot(object, object)
    def _on_loaded(self, media: MediaFile | None, error: str | None) -> None:
        if error:
            QMessageBox.warning(self, "파일 열기", error)
            return
        assert media is not None
        if any(f.path == media.path for f in self.files):
            return
        self.files.append(media)
        item = QListWidgetItem(media.path.name)
        item.setToolTip(str(media.path))
        item.setIcon(icons.icon("film" if media.has_video else "music", self.p.muted))
        self.file_list.addItem(item)
        if self.current is None:
            self.file_list.setCurrentRow(len(self.files) - 1)
        self.status.setText(f"{len(self.files)}개 파일")

    def _on_file_row(self, row: int) -> None:
        self.current = self.files[row] if 0 <= row < len(self.files) else None
        self.sel_in = self.sel_out = None
        m = self.current
        if m is None:
            self.player.load(None)
            self.timeline.set_duration(0)
            self.file_info.setText("")
        else:
            self.player.load(m.path, m.duration, m.fps)
            self.timeline.set_duration(m.duration)
            parts = [format_time(m.duration, millis=False)]
            if m.has_video:
                parts.append(f"{m.width}×{m.height} · {m.fps:.3g}fps · {m.vcodec}")
            parts.append(f"소리 {m.acodec} {m.sample_rate // 1000 if m.sample_rate else ''}kHz" if m.has_audio else "소리 없음")
            self.file_info.setText("\n".join(parts))
        self._refresh_view()

    def remove_current(self) -> None:
        row = self.file_list.currentRow()
        if row < 0:
            return
        media = self.files.pop(row)
        self.segments.pop(media.path, None)
        self.split_points.pop(media.path, None)
        self.file_list.takeItem(row)
        if not self.files:
            self._on_file_row(-1)

    def _move_file(self, delta: int) -> None:
        row = self.file_list.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < len(self.files):
            return
        self.files.insert(new, self.files.pop(row))
        item = self.file_list.takeItem(row)
        self.file_list.insertItem(new, item)
        self.file_list.setCurrentRow(new)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        projects = [p for p in paths if p.suffix.lower() == PROJECT_EXT]
        if projects:
            self.load_project(projects[0])
            return
        self.open_files([p for p in paths if p.suffix.lower() in MEDIA_EXTS])

    # ── 표시·구간 ─────────────────────────────────────────────────────────
    def _on_position(self, t: float) -> None:
        self.timeline.set_position(t)

    def _on_timeline_selection(self, a: float, b: float) -> None:
        self.sel_in = a if a >= 0 else None
        self.sel_out = b if b >= 0 else None
        self._refresh_marks()

    def mark_in(self) -> None:
        if self.current:
            self.sel_in = self.player.position()
            if self.sel_out is not None and self.sel_out < self.sel_in:
                self.sel_out = None
            self._refresh_marks()

    def mark_out(self) -> None:
        if self.current:
            self.sel_out = self.player.position()
            if self.sel_in is not None and self.sel_in > self.sel_out:
                self.sel_in = None
            self._refresh_marks()

    def _selection(self) -> Segment | None:
        if self.current is None or (self.sel_in is None and self.sel_out is None):
            return None
        seg = Segment(self.sel_in or 0.0, self.sel_out if self.sel_out is not None else self.current.duration)
        return seg if seg.duration >= 0.05 else None

    def add_segment(self) -> None:
        if self.tool == "split":
            self.add_split_point()
            return
        seg = self._selection()
        if seg is None or self.current is None:
            self.status.setText("먼저 시작점([)과 끝점(])을 표시해 주세요.")
            return
        segs = self.segments.setdefault(self.current.path, [])
        segs.append(seg)
        self.segments[self.current.path] = normalize(segs, self.current.duration)
        self.sel_in = self.sel_out = None
        self._refresh_view()

    def delete_segment(self) -> None:
        item = self.seg_tree.currentItem()
        if self.current is None or item is None:
            return
        segs = self.segments.get(self.current.path, [])
        idx = self.seg_tree.indexOfTopLevelItem(item)
        if 0 <= idx < len(segs):
            segs.pop(idx)
        self._refresh_view()

    def clear_segments(self) -> None:
        if self.current:
            self.segments.pop(self.current.path, None)
            self._refresh_view()

    def add_split_point(self) -> None:
        if self.current is None or self.tool != "split":
            return
        self.split_manual.setChecked(True)
        pts = self.split_points.setdefault(self.current.path, [])
        pts.append(self.player.position())
        self._refresh_view()

    def clear_split_points(self) -> None:
        if self.current:
            self.split_points.pop(self.current.path, None)
            self._refresh_view()

    def _split_parts(self, media: MediaFile) -> list[Segment]:
        if self.split_equal.isChecked():
            return split_equal(self.split_count.value(), media.duration)
        if self.split_every.isChecked():
            return split_every(self.split_secs.value(), media.duration)
        return split_at(self.split_points.get(media.path, []), media.duration)

    def _refresh_marks(self) -> None:
        self.in_label.setText(format_time(self.sel_in) if self.sel_in is not None else "--:--.---")
        self.out_label.setText(format_time(self.sel_out) if self.sel_out is not None else "--:--.---")
        seg = self._selection()
        self.sel_len.setText(f"선택 길이 {format_time(seg.duration)}" if seg else "")
        self.timeline.set_selection(self.sel_in, self.sel_out)

    def _refresh_view(self) -> None:
        m = self.current
        self._refresh_marks()
        if m is None:
            self.timeline.set_segments([], "keep")
            self.timeline.set_split_points([])
            self.seg_tree.clear()
            return
        if self.tool == "split":
            parts = self._split_parts(m)
            self.timeline.set_segments(parts, "part")
            self.timeline.set_split_points([s.start for s in parts[1:]])
            self.split_summary.setText(f"{len(parts)}개 파일로 나눕니다: " + ", ".join(format_time(s.duration, millis=False) for s in parts[:8])
                                       + (" …" if len(parts) > 8 else ""))  # fmt: skip
            return
        self.timeline.set_split_points([])
        segs = self.segments.get(m.path, [])
        self.timeline.set_segments(segs, "remove" if self.tool == "remove" else "keep")
        self.seg_tree.clear()
        for i, s in enumerate(segs, start=1):
            item = QTreeWidgetItem([str(i), format_time(s.start), format_time(s.end), format_time(s.duration)])
            item.setData(1, Qt.ItemDataRole.UserRole, s.start)
            self.seg_tree.addTopLevelItem(item)
        total = sum(s.duration for s in segs)
        role = {"remove": "지울 구간", "cut": "남길 구간"}.get(self.tool, "구간 (비우면 전체)")
        self.seg_title.setText(f"{role} {len(segs)}개 · 합계 {format_time(total, millis=False)}" if segs else role)

    # ── 작업 종류·출력 ─────────────────────────────────────────────────────
    def _select_tool(self, key: str) -> None:
        self.tool = key
        self.tool_buttons[key].setChecked(True)
        t = TOOL_BY_KEY[key]
        self.hint.setText(t.hint)
        self.stack.setCurrentIndex(1 if key == "split" else 0)
        self.add_seg_btn.setText("나누기 지점 추가 (S)" if key == "split" else "구간 추가 (Enter)")
        if key == "convert":
            self.mode_encode.setChecked(True)
        if key == "extract":
            self.opt_extract.setChecked(True)
        if key == "mute":
            self.opt_mute.setChecked(True)
        self.start_btn.setText({"cut": "자르기 시작", "remove": "구간 제거 시작", "split": "나누기 시작", "merge": "합치기 시작",
                                "extract": "소리 추출 시작", "mute": "소리 제거 시작", "convert": "변환 시작"}[key])  # fmt: skip
        self._refresh_output_controls()
        self._refresh_view()

    def _refresh_output_controls(self) -> None:
        k = self.tool
        self.mode_box.setVisible(k not in ("extract", "convert"))
        encode = self.mode_encode.isChecked() or k == "convert"
        self.enc_box.setVisible(encode and k != "extract")
        self.opt_merge.setVisible(k == "cut")
        self.opt_extract.setVisible(k != "mute")
        self.audio_fmt.setVisible(k != "mute")
        self.opt_extract.setEnabled(k != "extract")
        self.opt_mute.setVisible(k not in ("extract", "mute"))
        self.opt_all.setVisible(k in ("extract", "mute", "convert"))
        self.opt_stamp.setVisible(k in ("cut", "remove", "split", "merge"))
        self.mode_hint.setText(
            "프레임 단위로 정확히 자르고 형식을 바꿀 수 있습니다. 시간이 더 걸립니다." if encode else
            "화질 손실 없이 빠르게 저장합니다. 시작점은 가까운 앞 키프레임으로 맞춰집니다."
        )  # fmt: skip

    def open_encode_settings(self) -> None:
        ff = self.ffmpeg()
        if ff is None:
            return
        dlg = EncodeDialog(self.encode, available_video_codecs(ff), available_audio_codecs(ff), self)
        if dlg.exec():
            self.encode = dlg.result_settings()
            self.enc_summary.setText(summarize(self.encode))

    def _browse_out(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.out_dir.text())
        if path:
            self.out_dir.setText(path)

    # ── 실행 ───────────────────────────────────────────────────────────────
    def _segments_or_whole(self, m: MediaFile) -> list[Segment]:
        segs = self.segments.get(m.path, [])
        if not segs and m is self.current and self._selection():
            segs = [self._selection()]
        return segs or [Segment(0.0, m.duration)]

    def build_jobs(self) -> list[EditJob]:
        if not self.files or self.current is None:
            raise EditError("먼저 편집할 파일을 열어 주세요.")
        t = TOOL_BY_KEY[self.tool]
        mode = "encode" if (self.tool == "convert" or self.mode_encode.isChecked()) and self.tool != "extract" else "fast"
        common = dict(mode=mode, encode=self.encode, suffix=t.suffix, save_timestamps=self.opt_stamp.isChecked() and self.opt_stamp.isVisible(),
                      extract_audio=self.audio_fmt.currentData() if self.opt_extract.isChecked() and self.opt_extract.isVisible() else None,
                      remove_audio=self.opt_mute.isChecked() and self.opt_mute.isVisible(),
                      base_name=self.out_name.text().strip() or None)  # fmt: skip

        def out_dir(m: MediaFile) -> Path:
            return m.path.parent if self.same_folder.isChecked() else Path(self.out_dir.text())

        m = self.current
        if self.tool == "cut":
            segs = self.segments.get(m.path) or ([self._selection()] if self._selection() else [])
            if not segs:
                raise EditError("남길 구간을 표시해 주세요. 시작점([)과 끝점(])을 찍고 '구간 추가'를 누르세요.")
            return [EditJob(tuple(Piece(m, s) for s in segs), out_dir(m), merge=self.opt_merge.isChecked(), **common)]
        if self.tool == "remove":
            remove = self.segments.get(m.path) or ([self._selection()] if self._selection() else [])
            if not remove:
                raise EditError("지울 구간을 표시해 주세요.")
            keep = complement(remove, m.duration)
            if not keep:
                raise EditError("모든 부분을 지우게 되어 남는 영상이 없습니다.")
            return [EditJob(tuple(Piece(m, s) for s in keep), out_dir(m), merge=True, **common)]
        if self.tool == "split":
            parts = self._split_parts(m)
            if len(parts) < 2:
                raise EditError("나눌 지점이 없습니다. 'S'로 지점을 추가하거나 균등/시간 나누기를 선택하세요.")
            return [EditJob(tuple(Piece(m, s) for s in parts), out_dir(m), merge=False, **common)]
        if self.tool == "merge":
            if len(self.files) < 2:
                raise EditError("합칠 파일을 2개 이상 추가해 주세요.")
            pieces = tuple(Piece(f, s) for f in self.files for s in self._segments_or_whole(f))
            return [EditJob(pieces, out_dir(self.files[0]), merge=True, **common)]
        targets = self.files if self.opt_all.isChecked() else [m]
        jobs = []
        for f in targets:
            pieces = tuple(Piece(f, s) for s in self._segments_or_whole(f))
            if self.tool == "extract":
                if not f.has_audio:
                    continue
                jobs.append(EditJob(pieces, out_dir(f), merge=True, save_video=False,
                                    **{**common, "extract_audio": self.audio_fmt.currentData(), "mode": "fast"}))  # fmt: skip
            elif self.tool == "mute":
                jobs.append(EditJob(pieces, out_dir(f), merge=True, **{**common, "remove_audio": True}))
            else:  # convert
                jobs.append(EditJob(pieces, out_dir(f), merge=True, **{**common, "mode": "encode"}))
        if not jobs:
            raise EditError("처리할 파일이 없습니다 (소리가 있는 파일이 없을 수 있습니다).")
        return jobs

    def start(self) -> None:
        if self._cancel is not None:
            return
        ff = self.ffmpeg()
        if ff is None:
            return
        try:
            jobs = self.build_jobs()
        except EditError as exc:
            QMessageBox.information(self, "vCAM 편집기", str(exc))
            return
        self.player.pause()
        self._cancel = threading.Event()
        cancel = self._cancel
        self._set_running(True)

        def work() -> None:
            outputs: list[Path] = []
            notes: list[str] = []
            try:
                for i, job in enumerate(jobs):
                    def prog(frac: float, label: str, i: int = i) -> None:
                        self._bridge.progress.emit((i + frac) / len(jobs), f"{label} ({i + 1}/{len(jobs)})" if len(jobs) > 1 else label)
                    r = execute(job, ff, cancel, prog)
                    outputs += r.outputs
                    notes += r.notes
                self._bridge.done.emit((outputs, notes), None)
            except Cancelled:
                self._bridge.done.emit((outputs, notes), "취소했습니다.")
            except EditError as exc:
                self._bridge.done.emit((outputs, notes), str(exc))
            except Exception as exc:  # noqa: BLE001 - 작업 스레드 최상위
                log.exception("편집 작업 실패")
                self._bridge.done.emit((outputs, notes), f"예기치 않은 오류: {exc!r}")

        threading.Thread(target=work, name="vcam-edit-job", daemon=True).start()

    def cancel_job(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.status.setText("취소하는 중…")

    def _set_running(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.cancel_btn.setVisible(running)
        self.start_btn.setEnabled(not running)
        for b in self.tool_buttons.values():
            b.setEnabled(not running)
        if running:
            self.progress.setValue(0)
            self.open_result.hide()
            self.open_result_dir.hide()

    @Slot(float, str)
    def _on_progress(self, frac: float, label: str) -> None:
        self.progress.setValue(int(frac * 1000))
        self.status.setText(f"{label or '처리 중'}… {frac * 100:.0f}%")

    @Slot(object, object)
    def _on_done(self, result, error: str | None) -> None:
        outputs, notes = result
        self._cancel = None
        self._set_running(False)
        self._outputs = outputs
        if outputs:
            self.open_result.show()
            self.open_result_dir.show()
        if error:
            self.status.setText(error)
            if error != "취소했습니다.":
                QMessageBox.warning(self, "vCAM 편집기", error)
            return
        names = ", ".join(p.name for p in outputs[:3]) + (f" 외 {len(outputs) - 3}개" if len(outputs) > 3 else "")
        self.status.setText(f"완료: {names}" + (f"  ·  {notes[0]}" if notes else ""))

    def _open_result(self) -> None:
        if self._outputs:
            os.startfile(self._outputs[0])  # noqa: S606

    def _reveal_result(self) -> None:
        if self._outputs:
            reveal_in_explorer(self._outputs[0])

    def capture(self) -> None:
        ff = self.ffmpeg()
        if ff is None or self.current is None or not self.current.has_video:
            return
        media, at = self.current, self.player.position()
        out = media.path.parent if self.same_folder.isChecked() else Path(self.out_dir.text())

        def work() -> None:
            try:
                self._bridge.captured.emit(capture_frame(ff, media, at, out))
            except EditError as exc:
                self._bridge.captured.emit(str(exc))

        threading.Thread(target=work, name="vcam-capture-frame", daemon=True).start()

    @Slot(object)
    def _on_captured(self, result) -> None:
        if isinstance(result, Path):
            self._outputs = [result]
            self.open_result.show()
            self.open_result_dir.show()
            self.status.setText(f"프레임 저장: {result.name}")
        else:
            QMessageBox.warning(self, "프레임 저장", str(result))

    # ── 프로젝트 ───────────────────────────────────────────────────────────
    def save_project(self) -> None:
        if not self.files:
            return
        path, _ = QFileDialog.getSaveFileName(self, "프로젝트 저장", str(self.files[0].path.with_suffix(PROJECT_EXT)),
                                              f"vCAM 편집 프로젝트 (*{PROJECT_EXT})")  # fmt: skip
        if not path:
            return
        ProjectFile(
            files=[f.path for f in self.files],
            segments={str(k): v for k, v in self.segments.items()},
            split_points={str(k): v for k, v in self.split_points.items()},
            tool=self.tool, mode="encode" if self.mode_encode.isChecked() else "fast", encode=self.encode,
            options={"merge": self.opt_merge.isChecked(), "stamp": self.opt_stamp.isChecked()},
        ).save(Path(path))  # fmt: skip
        self.status.setText(f"프로젝트 저장: {Path(path).name}")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", f"vCAM 편집 프로젝트 (*{PROJECT_EXT})")
        if path:
            self.load_project(Path(path))

    def load_project(self, path: Path) -> None:
        try:
            proj = ProjectFile.load(path)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "프로젝트 열기", f"프로젝트를 읽지 못했습니다: {exc}")
            return
        missing = [f for f in proj.files if not f.exists()]
        if missing:
            QMessageBox.warning(self, "프로젝트 열기", "찾을 수 없는 파일이 있습니다:\n" + "\n".join(str(m) for m in missing[:5]))
        self.files.clear()
        self.file_list.clear()
        self.current = None
        self.segments = {Path(k): v for k, v in proj.segments.items()}
        self.split_points = {Path(k): v for k, v in proj.split_points.items()}
        self.encode = proj.encode
        self.enc_summary.setText(summarize(self.encode))
        (self.mode_encode if proj.mode == "encode" else self.mode_fast).setChecked(True)
        self.opt_merge.setChecked(proj.options.get("merge", True))
        self.opt_stamp.setChecked(proj.options.get("stamp", False))
        self._select_tool(proj.tool if proj.tool in TOOL_BY_KEY else "cut")
        self.open_files([f for f in proj.files if f.exists()])

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._cancel is not None:
            if QMessageBox.question(self, "vCAM 편집기", "작업이 진행 중입니다. 취소하고 닫을까요?") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancel.set()
        self.player.pause()
        event.accept()


