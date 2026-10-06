"""메인 창. UI는 RecordingController만 호출하고 백엔드 라이브러리를 직접 다루지 않는다."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QIcon,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vcam import GITHUB_REPO, __version__
from vcam.audio.worker import WorkerStatus
from vcam.capture.region import list_monitors
from vcam.domain.events import UserFacingError
from vcam.domain.models import (
    ENCODER_LABELS,
    QUALITY_LABELS,
    CaptureSource,
    MonitorInfo,
    RecordingMetrics,
    RecordingResult,
    Rect,
)
from vcam.domain.states import RecordingState
from vcam.encoding.encoder_probe import select_encoder
from vcam.encoding.ffmpeg import find_ffmpeg
from vcam.platform.windows.dpi import ScreenMapper
from vcam.platform.windows.hotkeys import HotkeyManager
from vcam.services.profile_service import ProfileService, Settings
from vcam.services.recording_controller import RecordingController
from vcam.services.recovery_service import find_incomplete_sessions
from vcam.ui import icons
from vcam.ui.dialogs import (
    AboutDialog,
    DiagnosticsDialog,
    RecoveryDialog,
    SettingsDialog,
    show_consent_notice,
)
from vcam.ui.editor.editor_window import EditorWindow
from vcam.ui.theme import apply_theme
from vcam.ui.tokens import Palette
from vcam.ui.updater import PreparedUpdate, Updater
from vcam.ui.widgets.audio_meter import AudioMeter
from vcam.ui.widgets.guide_frame import GuideFrame
from vcam.ui.widgets.preview import PreviewSampler, PreviewView
from vcam.ui.widgets.recent_recordings import RecentRecordings, reveal_in_explorer
from vcam.ui.widgets.recording_bar import CountdownOverlay, RecordingBar
from vcam.ui.widgets.region_overlay import RegionSelector
from vcam.util.paths import assets_dir, format_bytes, format_duration, logs_dir

log = logging.getLogger(__name__)

STATE_TEXT = {
    RecordingState.IDLE: ("대상 선택 필요", "info", "muted"),
    RecordingState.SELECTING: ("대상 선택 중", "region", "cyan"),
    RecordingState.READY: ("녹화 준비됨", "check", "ok"),
    RecordingState.COUNTDOWN: ("곧 시작합니다", "clock", "warn"),
    RecordingState.RECORDING: ("녹화 중", "record", "rec"),
    RecordingState.PAUSED: ("일시정지", "pause", "warn"),
    RecordingState.FINALIZING: ("저장 중…", "film", "cyan"),
    RecordingState.RECOVERING: ("복구 중…", "lifebuoy", "warn"),
    RecordingState.REVIEW: ("저장 완료", "check", "ok"),
    RecordingState.ERROR: ("오류", "alert", "rec"),
}


class _Bridge(QObject):
    encoder_selected = Signal(str)
    devices_loaded = Signal(object)  # (speakers, mics) 또는 오류 문자열


class MainWindow(QMainWindow):
    def __init__(self, profiles: ProfileService, settings: Settings, palette_tokens: Palette) -> None:
        super().__init__()
        self.profiles = profiles
        self.settings = settings
        self.p = palette_tokens
        self.mapper = ScreenMapper()
        self.monitors: list[MonitorInfo] = []
        self.controller = RecordingController(settings, self)
        self._themed: list[tuple[object, str, str]] = []  # (위젯/액션, 아이콘 이름, 색 토큰)
        self._last_result: RecordingResult | None = None
        self.editor: EditorWindow | None = None
        self._selector: RegionSelector | None = None
        self._was_minimized_for_recording = False

        self.setWindowTitle("vCAM")
        self.setWindowIcon(QIcon(str(assets_dir() / "icons" / "vcam.ico")))
        self.resize(1100, 780)
        self.setMinimumSize(900, 680)

        self.guide = GuideFrame(self.p, self.mapper)
        self.guide.region_changed.connect(self._on_guide_changed)
        self.guide.reselect_requested.connect(self.select_region)
        self.bar = RecordingBar(self.p)
        self.bar.pause_clicked.connect(self.controller.toggle_pause)
        self.bar.stop_clicked.connect(self.controller.stop)
        self.bar.restore_clicked.connect(self._restore_window)
        self.bar.mic_clicked.connect(self._toggle_mic_mute)
        self.countdown = CountdownOverlay(self.p)
        self.countdown.cancel_clicked.connect(self.controller.cancel_countdown)

        self.sampler = PreviewSampler(self)
        self._build_menu()
        self._build_ui()
        self._save_settings(self.settings)
        self.sampler.frame_ready.connect(self.preview.set_image)

        c = self.controller
        c.state_changed.connect(self._on_state)
        c.countdown_tick.connect(self._on_countdown)
        c.metrics_changed.connect(self._on_metrics)
        c.recording_saved.connect(self._on_saved)
        c.error_raised.connect(self._on_error)
        c.notice.connect(lambda text: self.statusBar().showMessage(text, 5000))

        self.hotkeys = HotkeyManager(self)
        self.hotkeys.activated.connect(self._on_hotkey)
        ok_f9 = self.hotkeys.register("start_stop", "F9")
        ok_f10 = self.hotkeys.register("pause", "F10")
        if not (ok_f9 and ok_f10):
            self.statusBar().showMessage("일부 전역 단축키(F9/F10)를 등록하지 못했습니다. 다른 프로그램이 사용 중일 수 있습니다.", 8000)

        self._bridge = _Bridge(self)
        self._bridge.encoder_selected.connect(self._on_encoder_selected)
        self._bridge.devices_loaded.connect(self._on_devices_loaded)
        self._meter_timer = QTimer(self, interval=66)
        self._meter_timer.timeout.connect(self._update_meters)
        self._meter_timer.start()
        self._load_audio_devices()

        self._apply_icons()
        self._reload_monitors()
        self._restore_source()
        self._on_state(self.controller.state)
        self.recent.refresh(Path(self.settings.output_dir), find_ffmpeg(self.settings.ffmpeg_path))
        self._detect_encoder()
        QGuiApplication.instance().screenAdded.connect(lambda _s: self._on_screens_changed())
        QGuiApplication.instance().screenRemoved.connect(lambda _s: self._on_screens_changed())
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _s: self._on_system_theme())
        self.updater = Updater(self)
        self.updater.prepared.connect(self._on_update_prepared)
        self.updater.available.connect(self._on_update_available)
        self.updater.up_to_date.connect(
            lambda v: QMessageBox.information(self, "업데이트 확인", f"최신 버전(v{v})을 사용하고 있습니다.")
        )
        self.updater.failed.connect(lambda msg: QMessageBox.warning(self, "업데이트 확인", msg))
        self.updater.progress.connect(
            lambda pct: self.statusBar().showMessage(f"새 버전 내려받는 중… {pct}%", 2000)
        )
        self._apply_update_on_exit: PreparedUpdate | None = None
        self._restart_after_update = False
        QTimer.singleShot(300, self._startup_checks)
        if self.settings.auto_update:
            QTimer.singleShot(5000, lambda: self.updater.check(manual=False))

    # ── 구성 ───────────────────────────────────────────────────────────────
    def _themed_icon(self, target, name: str, color: str = "text") -> None:
        self._themed.append((target, name, color))

    def _action(self, menu, text: str, slot, icon_name: str | None = None, shortcut_text: str = "") -> QAction:
        action = QAction(text + (f"\t{shortcut_text}" if shortcut_text else ""), self)
        action.triggered.connect(slot)
        if icon_name:
            self._themed_icon(action, icon_name)
        menu.addAction(action)
        return action

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("파일(&F)")
        self._action(m_file, "저장 폴더 열기", self.open_output_dir, "folder")
        self._action(m_file, "저장 폴더 변경…", self.change_output_dir, "folder_edit")
        m_file.addSeparator()
        self._action(m_file, "종료", self.close, "exit", "Alt+F4")

        m_rec = mb.addMenu("녹화(&R)")
        self.act_start = self._action(m_rec, "녹화 시작", self.controller.toggle_start_stop, "record", "F9")
        self.act_pause = self._action(m_rec, "일시정지", self.controller.toggle_pause, "pause", "F10")
        m_rec.addSeparator()
        self.monitor_menu = m_rec.addMenu("전체 화면 녹화")
        self._themed_icon(self.monitor_menu, "monitor")
        self.act_region = self._action(m_rec, "녹화 영역 지정…", self.select_region, "region")

        m_view = mb.addMenu("보기(&V)")
        self.act_guide = self._action(m_view, "가이드 프레임 표시", self._toggle_guide, "guide")
        self.act_guide.setCheckable(True)
        self.act_guide.setChecked(self.settings.show_guide_frame)
        m_theme = m_view.addMenu("테마")
        self._themed_icon(m_theme, "theme")
        group = QActionGroup(self)
        for key, label in (("system", "시스템 설정 따르기"), ("light", "라이트"), ("dark", "다크")):
            a = QAction(label, self, checkable=True)
            a.setChecked(self.settings.theme == key)
            a.triggered.connect(lambda _c=False, k=key: self._set_theme(k))
            group.addAction(a)
            m_theme.addAction(a)

        m_tools = mb.addMenu("도구(&T)")
        act_edit = self._action(m_tools, "동영상 편집기 (자르기·합치기·변환)…", lambda: self.open_editor(), "scissors", "Ctrl+E")
        act_edit.setShortcut("Ctrl+E")
        m_tools.addSeparator()
        self._action(m_tools, "시스템 진단…", self.open_diagnostics, "pulse")
        self._action(m_tools, "미완료 녹화 복구…", lambda: self.open_recovery(manual=True), "lifebuoy")
        self._action(m_tools, "로그 폴더 열기", lambda: os.startfile(logs_dir()), "log")  # noqa: S606
        m_tools.addSeparator()
        self._action(m_tools, "설정…", self.open_settings, "settings")

        m_help = mb.addMenu("도움말(&H)")
        self._action(m_help, "사용 안내", lambda: AboutDialog(self.p, __version__, self, help_mode=True).exec(), "help")
        self._action(m_help, "사용설명서 (온라인)", self.open_manual, "log")
        self._action(m_help, "녹화 동의 안내", lambda: show_consent_notice(self), "info")
        self._action(m_help, "업데이트 확인…", lambda: self.updater.check(manual=True), "refresh")
        m_help.addSeparator()
        self._action(m_help, "vCAM 정보", lambda: AboutDialog(self.p, __version__, self).exec(), "info")

    def _tool(self, icon_name: str, tip: str, slot, color: str = "text", size: int = 20) -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setIconSize(QSize(size, size))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        self._themed_icon(b, icon_name, color)
        return b

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        return frame, layout

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 8, 16, 0)
        outer.setSpacing(12)

        # 상단: 브랜드 + 모드 + 아이콘 버튼
        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QIcon(str(assets_dir() / "icons" / "vcam.png")).pixmap(30, 30))
        brand = QLabel("vCAM")
        brand.setProperty("role", "brand")
        top.addWidget(logo)
        top.addWidget(brand)
        top.addSpacing(20)
        self.mode_group = QButtonGroup(self)
        self.mode_display = self._mode_button("화면", "monitor", "모니터 전체를 녹화합니다")
        self.mode_region = self._mode_button("영역", "region", "마우스로 지정한 영역만 녹화합니다")
        self.mode_display.clicked.connect(self._on_mode_display)
        self.mode_region.clicked.connect(self.select_region)
        top.addWidget(self.mode_display)
        top.addWidget(self.mode_region)
        top.addStretch(1)
        edit_btn = self._tool("scissors", "동영상 편집기 (Ctrl+E)", lambda: self.open_editor())
        edit_btn.setText("편집기")
        edit_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        top.addWidget(edit_btn)
        top.addWidget(self._tool("pulse", "시스템 진단", self.open_diagnostics))
        top.addWidget(self._tool("settings", "설정", self.open_settings))
        top.addWidget(self._tool("help", "사용 안내", lambda: AboutDialog(self.p, __version__, self, help_mode=True).exec()))
        outer.addLayout(top)

        # 가운데: 미리보기 + 준비 패널
        middle = QHBoxLayout()
        middle.setSpacing(12)
        prev_card, prev_layout = self._card()
        self.preview = PreviewView(self.p)
        prev_layout.addWidget(self.preview)
        middle.addWidget(prev_card, 3)

        info_card, info = self._card()
        info_card.setMinimumWidth(330)
        head = QHBoxLayout()
        self.state_icon = QLabel()
        self.state_label = QLabel()
        self.state_label.setProperty("role", "section")
        head.addWidget(self.state_icon)
        head.addWidget(self.state_label)
        head.addStretch(1)
        info.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(2, 1)
        self.monitor_combo = QComboBox()
        self.monitor_combo.currentIndexChanged.connect(self._on_monitor_combo)
        self.region_label = QLabel()
        self.region_label.setProperty("role", "value")
        self.reselect_btn = self._tool("region", "영역 다시 지정", self.select_region, size=18)
        target_box = QHBoxLayout()
        target_box.addWidget(self.monitor_combo, 1)
        target_box.addWidget(self.region_label, 1)
        target_box.addWidget(self.reselect_btn)
        self.size_label = QLabel("—")
        self.size_label.setProperty("role", "value")
        self.fps_combo = QComboBox()
        for fps in (15, 24, 30, 60):
            self.fps_combo.addItem(f"{fps} FPS", fps)
        self.fps_combo.setCurrentIndex(max(0, self.fps_combo.findData(self.settings.profile.fps)))
        self.fps_combo.currentIndexChanged.connect(self._on_profile_combo)
        self.quality_combo = QComboBox()
        for key, label in QUALITY_LABELS.items():
            self.quality_combo.addItem(label, key)
        self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(self.settings.profile.quality_preset)))
        self.quality_combo.currentIndexChanged.connect(self._on_profile_combo)
        self.encoder_label = QLabel("확인 중…")
        self.encoder_label.setProperty("role", "muted")
        self.output_label = QLabel()
        self.output_label.setProperty("role", "muted")
        self.output_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        out_box = QHBoxLayout()
        out_box.addWidget(self.output_label, 1)
        out_box.addWidget(self._tool("folder", "저장 폴더 열기", self.open_output_dir, size=18))
        out_box.addWidget(self._tool("folder_edit", "저장 폴더 변경", self.change_output_dir, size=18))

        quality_box = QHBoxLayout()
        quality_box.addWidget(self.fps_combo, 1)
        quality_box.addWidget(self.quality_combo, 1)
        rows = [
            ("monitor", "대상", target_box),
            ("resize", "크기", self.size_label),
            ("sparkle", "화질", quality_box),
            ("film", "인코더", self.encoder_label),
            ("folder", "저장", out_box),
        ]
        for r, (icon_name, label, widget) in enumerate(rows):
            ic = QLabel()
            ic.setFixedSize(20, 20)
            self._themed_icon(ic, icon_name, "muted")
            lb = QLabel(label)
            lb.setProperty("role", "muted")
            grid.addWidget(ic, r, 0)
            grid.addWidget(lb, r, 1)
            if isinstance(widget, QHBoxLayout):
                grid.addLayout(widget, r, 2)
            else:
                grid.addWidget(widget, r, 2)
        info.addLayout(grid)

        self._build_audio_section(info)
        self.ffmpeg_warning = QPushButton("FFmpeg를 찾을 수 없습니다 — 진단 열기")
        self.ffmpeg_warning.clicked.connect(self.open_diagnostics)
        self.ffmpeg_warning.hide()
        info.addWidget(self.ffmpeg_warning)
        info.addStretch(1)
        middle.addWidget(info_card, 2)
        outer.addLayout(middle, 3)

        # 최근 녹화
        recent_card, recent_layout = self._card()
        rh = QHBoxLayout()
        title = QLabel("최근 녹화")
        title.setProperty("role", "section")
        self.recent_count = QLabel("0")
        self.recent_count.setProperty("role", "badge")
        rh.addWidget(title)
        rh.addWidget(self.recent_count)
        rh.addStretch(1)
        rh.addWidget(self._tool("play", "재생", lambda: self.recent.play(), size=18))
        rh.addWidget(self._tool("scissors", "선택한 녹화를 편집기에서 열기",
                                lambda: self.open_editor([self.recent.selected_path()] if self.recent.selected_path() else None), size=18))
        rh.addWidget(self._tool("folder", "폴더에서 보기", self._reveal_selected, size=18))
        rh.addWidget(self._tool("trash", "휴지통으로 이동", lambda: self.recent.trash(), size=18))
        rh.addWidget(self._tool("refresh", "새로 고침", self.refresh_recent, size=18))
        recent_layout.addLayout(rh)
        self.recent = RecentRecordings(self.p)
        self.recent.count_changed.connect(lambda n: self.recent_count.setText(str(n)))
        self.recent.edit_requested.connect(lambda path: self.open_editor([path]))
        recent_layout.addWidget(self.recent)
        outer.addWidget(recent_card, 2)

        # 하단 바
        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(16, 10, 16, 10)
        self.timer_label = QLabel("00:00")
        self.timer_label.setProperty("role", "timer")
        self.metrics_label = QLabel("")
        self.metrics_label.setProperty("role", "muted")
        self.result_label = QLabel("")
        self.result_play = self._tool("play", "방금 녹화한 파일 재생", self._play_last, size=18)
        self.result_folder = self._tool("folder", "방금 녹화한 파일 위치 열기", self._reveal_last, size=18)
        self.result_edit = self._tool("scissors", "방금 녹화한 파일 편집", lambda: self.open_editor([self._last_result.path] if self._last_result else None), size=18)
        bl.addWidget(self.timer_label)
        bl.addSpacing(12)
        bl.addWidget(self.metrics_label)
        bl.addWidget(self.result_label)
        bl.addWidget(self.result_play)
        bl.addWidget(self.result_folder)
        bl.addWidget(self.result_edit)
        bl.addStretch(1)
        hint = QLabel("F9 시작/종료 · F10 일시정지")
        hint.setProperty("role", "muted")
        bl.addWidget(hint)
        bl.addSpacing(12)
        self.pause_btn = QPushButton()
        self.pause_btn.setProperty("role", "round")
        self.pause_btn.setToolTip("일시정지 / 재개 (F10)")
        self.pause_btn.setAccessibleName("일시정지 또는 재개")
        self.pause_btn.setIconSize(QSize(20, 20))
        self.pause_btn.clicked.connect(self.controller.toggle_pause)
        self.record_btn = QPushButton("녹화 시작")
        self.record_btn.setObjectName("recordButton")
        self.record_btn.setAccessibleName("녹화 시작 또는 종료")
        self.record_btn.setIconSize(QSize(18, 18))
        self.record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_btn.clicked.connect(self.controller.toggle_start_stop)
        bl.addWidget(self.pause_btn)
        bl.addWidget(self.record_btn)

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(12)
        cl.addWidget(root, 1)
        cl.addWidget(bottom)
        self.setCentralWidget(container)

    def _mode_button(self, text: str, icon_name: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setProperty("role", "mode")
        b.setCheckable(True)
        b.setIconSize(QSize(20, 20))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_group.addButton(b)
        self._themed.append((b, icon_name, "mode"))
        return b

    def _apply_icons(self) -> None:
        p = self.p
        colors = {"text": p.text, "muted": p.muted, "accent": p.accent, "rec": p.rec, "cyan": p.cyan, "ok": p.ok, "warn": p.warn}
        for target, name, color in self._themed:
            if color == "mode":
                ic = icons.icon(name, p.muted, p.border, p.accent)
            else:
                ic = icons.icon(name, colors[color], p.border)
            if isinstance(target, QLabel):
                target.setPixmap(ic.pixmap(20, 20))
            else:
                target.setIcon(ic)
        self._update_state_widgets(self.controller.state)

    # ── 테마 ───────────────────────────────────────────────────────────────
    def _set_theme(self, key: str) -> None:
        self.settings = self.profiles.update(self.settings, theme=key)
        self._reapply_theme()

    def _on_system_theme(self) -> None:
        if self.settings.theme == "system":
            self._reapply_theme()

    def _reapply_theme(self) -> None:
        self.p = apply_theme(QApplication.instance(), self.settings.theme)
        icons.pixmap.cache_clear()
        for w in (self.preview, self.recent, self.guide, *self.meters.values()):
            w.set_palette(self.p)
        self.bar.apply_palette(self.p)
        if self.editor is not None:
            self.editor.set_palette(self.p)
        self.countdown.p = self.p
        self._apply_icons()

    # ── 대상 선택 ─────────────────────────────────────────────────────────
    def _reload_monitors(self) -> None:
        self.mapper.refresh()
        self.monitors = list_monitors()
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.clear()
        self.monitor_menu.clear()
        for m in self.monitors:
            self.monitor_combo.addItem(m.label, m.index)
            self.monitor_menu.addAction(m.label, lambda _c=False, i=m.index: self._choose_monitor(i))
        self.monitor_combo.blockSignals(False)

    def _on_screens_changed(self) -> None:
        if self.controller.is_busy:
            self.statusBar().showMessage("녹화 중 디스플레이 구성이 바뀌었습니다. 결과를 확인해 주세요.", 8000)
            return
        self._reload_monitors()
        self._restore_source()

    def _restore_source(self) -> None:
        if self.settings.source_kind == "region" and self.settings.last_region_rect is not None:
            self._apply_region(self.settings.last_region_rect, save=False)
        else:
            self._choose_monitor(self.settings.monitor_index)

    def _choose_monitor(self, index: int) -> None:
        if self.controller.is_busy:
            return
        monitor = next((m for m in self.monitors if m.index == index), None) or (self.monitors[0] if self.monitors else None)
        if monitor is None:
            return
        self.controller.set_source(CaptureSource("display", monitor.rect, monitor.label, monitor.index))
        self.settings = self.profiles.update(self.settings, source_kind="display", monitor_index=monitor.index)
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.setCurrentIndex(max(0, self.monitor_combo.findData(monitor.index)))
        self.monitor_combo.blockSignals(False)
        self.guide.hide()
        self._source_changed()

    def _on_mode_display(self) -> None:
        self._choose_monitor(int(self.monitor_combo.currentData() or self.settings.monitor_index))

    def _on_monitor_combo(self) -> None:
        data = self.monitor_combo.currentData()
        if data is not None:
            self._choose_monitor(int(data))

    def select_region(self) -> None:
        if self.controller.is_busy or self._selector is not None:
            return
        if not self.controller.begin_selection():
            return
        self.mapper.refresh()
        self.guide.hide()
        self.hide()
        self._selector = RegionSelector(self.p, self.mapper, self.settings.last_region_rect)
        self._selector.selected.connect(self._on_region_selected)
        self._selector.cancelled.connect(self._on_region_cancelled)
        QTimer.singleShot(120, self._selector.open)  # 메인 창이 사라진 뒤 오버레이를 띄운다

    @Slot(object)
    def _on_region_selected(self, rect: Rect) -> None:
        self._selector = None
        self.show()
        self._apply_region(rect, save=True)

    @Slot()
    def _on_region_cancelled(self) -> None:
        self._selector = None
        self.show()
        self.controller.cancel_selection()
        if self.controller.source is not None and self.controller.source.kind == "region":
            self._show_guide(self.controller.source.rect)
        self._source_changed()

    def _apply_region(self, rect: Rect, save: bool) -> None:
        self.controller.set_source(CaptureSource("region", rect, "선택 영역"))
        if save:
            self.settings = self.profiles.update(self.settings, source_kind="region", last_region=rect.as_tuple())
        self._show_guide(rect)
        self._source_changed()

    def _show_guide(self, rect: Rect) -> None:
        if self.settings.show_guide_frame:
            self.guide.show_region(rect)
            self.guide.set_locked(False)

    @Slot(object)
    def _on_guide_changed(self, rect: Rect) -> None:
        if self.controller.is_busy:
            return
        self.controller.set_source(CaptureSource("region", rect, "선택 영역"))
        self.settings = self.profiles.update(self.settings, source_kind="region", last_region=rect.as_tuple())
        self._source_changed()

    def _toggle_guide(self) -> None:
        self.settings = self.profiles.update(self.settings, show_guide_frame=self.act_guide.isChecked())
        src = self.controller.source
        if self.settings.show_guide_frame and src is not None and src.kind == "region":
            self._show_guide(src.rect)
        elif not self.controller.is_busy:
            self.guide.hide()

    def _source_changed(self) -> None:
        src = self.controller.source
        is_region = src is not None and src.kind == "region"
        self.mode_region.setChecked(is_region)
        self.mode_display.setChecked(not is_region)
        self.monitor_combo.setVisible(not is_region)
        self.region_label.setVisible(is_region)
        self.reselect_btn.setVisible(is_region)
        if src is not None:
            self.size_label.setText(f"{src.rect.width} × {src.rect.height} px")
            self.region_label.setText(f"영역 ({src.rect.left}, {src.rect.top})")
            self.sampler.set_rect(src.rect)
        self._update_state_widgets(self.controller.state)

    # ── 소리 ───────────────────────────────────────────────────────────────
    def _build_audio_section(self, info: QVBoxLayout) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {self.p.border};")
        info.addWidget(line)
        self.audio_toggles: dict[str, QToolButton] = {}
        self.audio_combos: dict[str, QComboBox] = {}
        self.meters: dict[str, AudioMeter] = {}
        for kind, label in (("system", "시스템 소리"), ("microphone", "마이크")):
            head = QHBoxLayout()
            ic = QLabel()
            ic.setFixedSize(20, 20)
            self._themed_icon(ic, "speaker" if kind == "system" else "mic", "muted")
            name = QLabel(label)
            name.setProperty("role", "muted")
            toggle = QToolButton()
            toggle.setCheckable(True)
            toggle.setProperty("role", "pill")
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setIconSize(QSize(16, 16))
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.setAccessibleName(f"{label} 녹음 켜기/끄기")
            toggle.clicked.connect(lambda _c=False, k=kind: self._on_audio_toggle(k))
            combo = QComboBox()
            combo.setAccessibleName(f"{label} 장치")
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            combo.addItem("장치 확인 중…", "")
            combo.currentIndexChanged.connect(lambda _i, k=kind: self._on_audio_device(k))
            meter = AudioMeter(self.p, label)
            head.addWidget(ic)
            head.addWidget(name)
            head.addWidget(combo, 1)
            head.addWidget(toggle)
            info.addLayout(head)
            row = QHBoxLayout()
            row.setContentsMargins(28, 0, 0, 4)
            row.addWidget(meter)
            info.addLayout(row)
            self.audio_toggles[kind], self.audio_combos[kind], self.meters[kind] = toggle, combo, meter
        self._refresh_audio_controls()

    def _audio_enabled(self, kind: str) -> bool:
        p = self.settings.profile
        return p.system_audio_enabled if kind == "system" else p.microphone_enabled

    def _refresh_audio_controls(self) -> None:
        busy = self.controller.is_busy
        for kind, toggle in self.audio_toggles.items():
            on = self._audio_enabled(kind)
            toggle.setChecked(on)
            toggle.setText("켜짐" if on else "꺼짐")
            name = ("speaker" if on else "speaker_off") if kind == "system" else ("mic" if on else "mic_off")
            toggle.setIcon(icons.icon(name, self.p.accent if on else self.p.muted, self.p.border))
            combo = self.audio_combos[kind]
            has_devices = combo.count() > 0 and combo.itemData(0) is not None
            toggle.setEnabled(not busy and (has_devices or on))
            combo.setEnabled(not busy and has_devices)

    def _load_audio_devices(self) -> None:
        def work() -> None:
            try:
                from vcam.audio.wasapi_backend import list_devices

                self._bridge.devices_loaded.emit(list_devices())
            except Exception as exc:  # noqa: BLE001 - UI에 원인을 보여준다
                log.warning("오디오 장치 목록 실패", exc_info=True)
                self._bridge.devices_loaded.emit(str(exc))

        threading.Thread(target=work, name="vcam-audio-devices", daemon=True).start()

    @Slot(object)
    def _on_devices_loaded(self, result: object) -> None:
        if isinstance(result, str):
            self.statusBar().showMessage(f"오디오 장치를 확인하지 못했습니다: {result}", 8000)
            speakers, mics = [], []
        else:
            speakers, mics = result
        for kind, devices, saved in (
            ("system", speakers, self.settings.system_audio_device),
            ("microphone", mics, self.settings.mic_device),
        ):
            combo = self.audio_combos[kind]
            combo.blockSignals(True)
            combo.clear()
            if devices:
                default = next((d for d in devices if d.is_default), None)
                combo.addItem(f"기본 장치 ({default.name})" if default else "기본 장치", "")
                for d in devices:
                    combo.addItem(d.name, d.id)
                combo.setCurrentIndex(max(0, combo.findData(saved)))
                for i in range(combo.count()):
                    combo.setItemData(i, combo.itemText(i), Qt.ItemDataRole.ToolTipRole)
            else:
                combo.addItem("연결된 마이크 없음" if kind == "microphone" else "출력 장치 없음", None)
            combo.blockSignals(False)
        self._refresh_audio_controls()

    def _on_audio_toggle(self, kind: str) -> None:
        on = self.audio_toggles[kind].isChecked()
        field = "system_audio_enabled" if kind == "system" else "microphone_enabled"
        self._save_settings(replace(self.settings, profile=replace(self.settings.profile, **{field: on})))
        self._refresh_audio_controls()
        if kind == "microphone" and on:
            self.statusBar().showMessage("마이크를 켰습니다. 녹음 전에 상대방의 동의가 필요할 수 있습니다.", 6000)

    def _on_audio_device(self, kind: str) -> None:
        data = self.audio_combos[kind].currentData()
        if data is None:
            return
        field = "system_audio_device" if kind == "system" else "mic_device"
        self._save_settings(replace(self.settings, **{field: data}))

    def _toggle_mic_mute(self) -> None:
        muted = not self.controller.audio.mic_muted
        self.controller.set_mic_muted(muted)
        self.bar.set_mic(self.settings.profile.microphone_enabled, muted)
        self.statusBar().showMessage("마이크 음소거" if muted else "마이크 음소거 해제", 3000)

    @Slot()
    def _update_meters(self) -> None:
        if self.isMinimized() or not self.isVisible():
            return
        audio = self.controller.audio
        for kind, meter in self.meters.items():
            if not self._audio_enabled(kind):
                meter.set_level(None)
                continue
            status = audio.status(kind)
            if status is WorkerStatus.OK:
                muted = kind == "microphone" and audio.mic_muted
                meter.set_level(audio.level(kind), "음소거" if muted else "")
            elif status is WorkerStatus.FAILED:
                worker = audio.worker(kind)
                meter.set_level(None, f"사용 불가 — {worker.error}" if worker and worker.error else "사용 불가")
            elif status is WorkerStatus.RECONNECTING:
                meter.set_level(None, "다시 연결 중…")
            else:
                meter.set_level(None, "연결 중…")

    # ── 설정 ───────────────────────────────────────────────────────────────
    def _on_profile_combo(self) -> None:
        profile = replace(
            self.settings.profile, fps=int(self.fps_combo.currentData()), quality_preset=self.quality_combo.currentData()
        )
        self._save_settings(replace(self.settings, profile=profile))

    def _save_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.profiles.save(settings)
        self.controller.update_settings(settings)
        self.output_label.setText(settings.output_dir)
        self.output_label.setToolTip(settings.output_dir)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.p, self)
        if dialog.exec():
            old = self.settings
            new = dialog.result_settings()
            self._save_settings(new)
            for combo, value in ((self.fps_combo, new.profile.fps), (self.quality_combo, new.profile.quality_preset)):
                combo.blockSignals(True)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                combo.blockSignals(False)
            self.act_guide.setChecked(new.show_guide_frame)
            self._toggle_guide()
            if (old.ffmpeg_path, old.profile.encoder_preference) != (new.ffmpeg_path, new.profile.encoder_preference):
                self._detect_encoder()
            if old.output_dir != new.output_dir:
                self.refresh_recent()

    def change_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.settings.output_dir)
        if path:
            self._save_settings(replace(self.settings, output_dir=path))
            self.refresh_recent()

    def open_output_dir(self) -> None:
        path = Path(self.settings.output_dir)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)  # noqa: S606

    def refresh_recent(self) -> None:
        self.recent.refresh(Path(self.settings.output_dir), find_ffmpeg(self.settings.ffmpeg_path))

    def _reveal_selected(self) -> None:
        path = self.recent.selected_path()
        if path:
            reveal_in_explorer(path)
        else:
            self.open_output_dir()

    def _detect_encoder(self) -> None:
        self.encoder_label.setText("확인 중…")
        settings = self.settings

        def work() -> None:
            ffmpeg = find_ffmpeg(settings.ffmpeg_path)
            if ffmpeg is None:
                self._bridge.encoder_selected.emit("!ffmpeg")
                return
            self._bridge.encoder_selected.emit(select_encoder(ffmpeg, settings.profile.encoder_preference).key)

        threading.Thread(target=work, name="vcam-encoder-probe", daemon=True).start()

    @Slot(str)
    def _on_encoder_selected(self, key: str) -> None:
        self.ffmpeg_warning.setVisible(key == "!ffmpeg")
        if key == "!ffmpeg":
            self.encoder_label.setText("FFmpeg 없음")
        elif not key:
            self.encoder_label.setText("사용 가능한 인코더 없음")
        else:
            prefix = "자동 → " if self.settings.profile.encoder_preference == "auto" else ""
            self.encoder_label.setText(prefix + ENCODER_LABELS[key])

    def open_diagnostics(self) -> None:
        dialog = DiagnosticsDialog(self.settings, self.p, self)
        dialog.report_ready.connect(lambda _r: self._detect_encoder())
        dialog.exec()

    def open_recovery(self, manual: bool = False) -> None:
        sessions = find_incomplete_sessions(exclude=self.controller.current_session_dir)
        if not sessions:
            if manual:
                QMessageBox.information(self, "미완료 녹화 복구", "복구할 미완료 녹화가 없습니다.")
            return
        dialog = RecoveryDialog(sessions, self.settings, self.p, self)
        dialog.exec()
        if dialog.recovered:
            self.refresh_recent()

    def _startup_checks(self) -> None:
        if not self.settings.first_run_notice_ack:
            show_consent_notice(self)
            self.settings = self.profiles.update(self.settings, first_run_notice_ack=True)
        self.open_recovery()

    # ── 녹화 상태 반영 ─────────────────────────────────────────────────────
    def _capture_area_logical(self):
        src = self.controller.source
        if src is None:
            return None
        return self.mapper.rect_to_logical(src.rect)[1]

    @Slot(object)
    def _on_state(self, state: RecordingState) -> None:
        self._update_state_widgets(state)
        area = self._capture_area_logical()
        src = self.controller.source
        recording = state in (RecordingState.RECORDING, RecordingState.PAUSED)
        if state is not RecordingState.COUNTDOWN:
            self.countdown.hide()
        if recording:
            if area is not None and not self.bar.isVisible():
                self.bar.show_near(area)
            self.bar.set_paused(state is RecordingState.PAUSED)
            self.bar.set_mic(self.settings.profile.microphone_enabled, self.controller.audio.mic_muted)
            if src is not None and src.kind == "region" and self.settings.show_guide_frame:
                self.guide.show_region(src.rect)
                self.guide.set_locked(True, "REC")
            self.sampler.set_paused(True)
            self.preview.set_overlay_text("● 녹화 중" if state is RecordingState.RECORDING else "❚❚ 일시정지")
            if state is RecordingState.RECORDING and self.settings.minimize_on_record and not self.isMinimized() \
                    and not self._was_minimized_for_recording:  # fmt: skip
                self._was_minimized_for_recording = True
                self.showMinimized()
        else:
            self.bar.hide()
            self.sampler.set_paused(False)
            self.preview.set_overlay_text("")
            if src is not None and src.kind == "region" and self.settings.show_guide_frame and state in (
                RecordingState.READY, RecordingState.REVIEW, RecordingState.ERROR, RecordingState.COUNTDOWN,
            ):  # fmt: skip
                self.guide.show_region(src.rect)
                self.guide.set_locked(state is RecordingState.COUNTDOWN, "곧 시작" if state is RecordingState.COUNTDOWN else "")
            if state in (RecordingState.REVIEW, RecordingState.ERROR, RecordingState.READY) and self._was_minimized_for_recording:
                self._was_minimized_for_recording = False
                self._restore_window()
        if state in (RecordingState.READY, RecordingState.COUNTDOWN):
            self.timer_label.setText("00:00")
            self.metrics_label.setText("")
        if state is not RecordingState.REVIEW:
            self.result_label.setText("")
            self.result_play.hide()
            self.result_folder.hide()
            self.result_edit.hide()

    def _update_state_widgets(self, state: RecordingState) -> None:
        text, icon_name, color_key = STATE_TEXT[state]
        color = getattr(self.p, color_key)
        self.state_icon.setPixmap(icons.pixmap(icon_name, color, 18, self.devicePixelRatioF()))
        self.state_label.setText(text)
        recording = state in (RecordingState.RECORDING, RecordingState.PAUSED)
        busy = self.controller.is_busy
        if recording:
            self.record_btn.setText("녹화 종료")
            self.record_btn.setIcon(icons.icon("stop", "#ffffff"))
            self.act_start.setText("녹화 종료\tF9")
        elif state is RecordingState.COUNTDOWN:
            self.record_btn.setText("취소")
            self.record_btn.setIcon(icons.icon("close", "#ffffff"))
            self.act_start.setText("카운트다운 취소\tF9")
        else:
            self.record_btn.setText("녹화 시작")
            self.record_btn.setIcon(icons.icon("record", "#ffffff"))
            self.act_start.setText("녹화 시작\tF9")
        self.record_btn.setProperty("recording", "true" if recording or state is RecordingState.COUNTDOWN else "false")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.record_btn.setEnabled(state not in (RecordingState.FINALIZING, RecordingState.RECOVERING, RecordingState.SELECTING))
        self.pause_btn.setEnabled(recording)
        self.pause_btn.setIcon(icons.icon("play" if state is RecordingState.PAUSED else "pause", self.p.text, self.p.border))
        self.act_pause.setEnabled(recording)
        self.act_pause.setText(("재개" if state is RecordingState.PAUSED else "일시정지") + "\tF10")
        for w in (self.mode_display, self.mode_region, self.monitor_combo, self.reselect_btn, self.fps_combo,
                  self.quality_combo, self.act_region, self.monitor_menu):  # fmt: skip
            w.setEnabled(not busy)
        if hasattr(self, "audio_toggles"):
            self._refresh_audio_controls()

    @Slot(int)
    def _on_countdown(self, value: int) -> None:
        area = self._capture_area_logical()
        if value > 0 and area is not None:
            self.countdown.show_value(value, area)
        else:
            self.countdown.hide()

    @Slot(object)
    def _on_metrics(self, m: RecordingMetrics) -> None:
        elapsed = format_duration(m.elapsed_ns / 1e9)
        self.timer_label.setText(elapsed)
        self.bar.set_elapsed(elapsed)
        if self.guide.isVisible() and self.controller.state is RecordingState.RECORDING:
            self.guide.set_status(f"REC {elapsed}")
        elif self.guide.isVisible() and self.controller.state is RecordingState.PAUSED:
            self.guide.set_status(f"일시정지 {elapsed}")
        parts = [format_bytes(m.file_bytes), f"{m.measured_fps:.0f} fps"]
        if m.frames_dropped:
            parts.append(f"드롭 {m.frames_dropped}")
        if m.encoder:
            parts.append(ENCODER_LABELS.get(m.encoder, m.encoder))
        self.metrics_label.setText("  ·  ".join(parts))

    @Slot(object)
    def _on_saved(self, result: RecordingResult) -> None:
        self._last_result = result
        info = result.media
        note = " (복구됨)" if result.recovered else ""
        self.result_label.setText(f"저장됨{note}: {result.path.name}  ·  {format_duration(info.duration_s)}  ·  {format_bytes(info.size_bytes)}")
        self.result_play.show()
        self.result_folder.show()
        self.result_edit.show()
        self.metrics_label.setText(f"드롭 {result.frames_dropped}" if result.frames_dropped else "")
        self.statusBar().showMessage(f"녹화를 저장했습니다: {result.path}", 10000)
        self.refresh_recent()

    @Slot(object)
    def _on_error(self, error: UserFacingError) -> None:
        self._restore_window()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("vCAM")
        box.setText(error.message)
        if error.detail:
            box.setDetailedText(error.detail)
        box.exec()

    def _play_last(self) -> None:
        if self._last_result and self._last_result.path.exists():
            os.startfile(self._last_result.path)  # noqa: S606

    def _reveal_last(self) -> None:
        if self._last_result:
            reveal_in_explorer(self._last_result.path)

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @Slot(str)
    def _on_hotkey(self, name: str) -> None:
        if self._selector is not None:
            return
        if name == "start_stop":
            self.controller.toggle_start_stop()
        elif name == "pause":
            self.controller.toggle_pause()

    # ── 편집기 ─────────────────────────────────────────────────────────────
    def open_editor(self, paths: list[Path] | None = None) -> None:
        if self.editor is None:
            self.editor = EditorWindow(self.p, lambda: find_ffmpeg(self.settings.ffmpeg_path))
            self.editor.setWindowIcon(self.windowIcon())
        self.editor.showNormal()
        self.editor.raise_()
        self.editor.activateWindow()
        if paths:
            self.editor.open_files([p for p in paths if p])

    # ── 업데이트 ───────────────────────────────────────────────────────────
    def open_manual(self) -> None:
        QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/blob/main/docs/USER_GUIDE.md"))

    @Slot(object)
    def _on_update_available(self, release) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("새 버전")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"vCAM v{release.version}이(가) 나왔습니다. (현재 v{__version__})")
        box.setInformativeText("이 실행 방식에서는 자동으로 설치할 수 없습니다. 릴리스 페이지에서 내려받아 주세요.")
        open_btn = box.addButton("릴리스 페이지 열기", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("닫기", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(release.html_url))

    @Slot(object)
    def _on_update_prepared(self, update: PreparedUpdate) -> None:
        self._apply_update_on_exit = update
        if self.controller.is_busy:
            self.statusBar().showMessage(f"새 버전 v{update.release.version}이 준비되었습니다. 앱을 닫으면 설치됩니다.", 15000)
            return
        notes = update.release.notes.strip()
        box = QMessageBox(self)
        box.setWindowTitle("업데이트 준비됨")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"새 버전 v{update.release.version}을(를) 내려받았습니다. (현재 v{__version__})")
        box.setInformativeText("지금 다시 시작해 업데이트할까요? 나중을 고르면 앱을 닫을 때 설치합니다.")
        if notes:
            box.setDetailedText(notes[:4000])
        now_btn = box.addButton("지금 업데이트", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is now_btn:
            self._restart_after_update = True
            self.close()

    # ── 종료 ───────────────────────────────────────────────────────────────
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.controller.is_busy:
            answer = QMessageBox.question(
                self, "vCAM 종료", "녹화가 진행 중입니다. 지금까지 녹화한 내용을 저장하고 종료할까요?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.controller.shutdown()
        finally:
            QApplication.restoreOverrideCursor()
        self.hotkeys.unregister_all()
        self.sampler.shutdown()
        for w in (self.guide, self.bar, self.countdown):
            w.close()
        if self.editor is not None:
            self.editor.close()
        if self._apply_update_on_exit is not None:
            try:
                self.updater.apply(self._apply_update_on_exit, restart=self._restart_after_update)
            except Exception as exc:  # noqa: BLE001 - 업데이트 실패가 종료를 막지 않게 한다
                log.error("업데이트 적용 실패: %s", exc)
        event.accept()
