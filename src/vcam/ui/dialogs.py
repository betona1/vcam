"""설정 · 시스템 진단 · 미완료 녹화 복구 · 정보/도움말 대화상자."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vcam.domain.models import ENCODER_LABELS, QUALITY_LABELS
from vcam.encoding.encoder_probe import clear_cache
from vcam.encoding.ffmpeg import find_ffmpeg
from vcam.services.profile_service import Settings
from vcam.services.recovery_service import IncompleteSession, discard_session, recover_session
from vcam.services.system_probe import ProbeReport, run_probe
from vcam.ui import icons
from vcam.ui.tokens import Palette
from vcam.util.paths import DEFAULT_FILENAME_TEMPLATE, assets_dir, format_bytes, render_filename

ASSETS = assets_dir()

CONSENT_TEXT = (
    "다른 사람의 대화나 화상 회의를 녹화하거나 녹음할 때는 상대방의 동의가 필요할 수 있습니다.\n"
    "녹화 전에 참여자에게 알리고, 저작권이 있는 콘텐츠는 허용된 범위에서만 녹화해 주세요.\n\n"
    "vCAM은 녹화한 화면과 소리를 사용자 PC 밖으로 보내지 않습니다."
)

VAVELING_URL = "https://vaveling.app"

ABOUT_TEXT = f"""
<p style="font-size:11pt"><b>중국어, 베이블링과 함께 시작하세요!</b></p>
<p>vCAM은 중국어 학습 앱 <b>베이블링</b>이 만든 무료 화면 녹화 프로그램입니다.<br>
캐릭터 베이블링과 함께 즐겁게 중국어를 배워 보세요.</p>
<p>👉 <a href="{VAVELING_URL}"><b>vaveling.app</b></a></p>
<hr>
<p style="color:gray">녹화 전 안내: 다른 사람의 대화나 화상 회의를 녹화하거나 녹음할 때는 상대방의 동의가 필요할 수 있습니다.
vCAM은 녹화한 화면과 소리를 사용자 PC 밖으로 보내지 않습니다.</p>
"""

HELP_TEXT = """<h3>빠른 사용법</h3>
<ol>
<li>위쪽 <b>화면</b> 또는 <b>영역</b> 버튼으로 녹화할 대상을 고릅니다.</li>
<li>영역 모드에서는 화면에 나타난 <b>가이드 프레임</b>의 모서리·변을 끌어 크기를 바꾸고, 위쪽 탭을 끌어 옮깁니다.
    탭을 더블클릭하면 영역을 처음부터 다시 지정합니다.</li>
<li><b>녹화 시작</b>(F9)을 누르면 3초 뒤 녹화가 시작됩니다. 일시정지/재개는 F10입니다.</li>
<li>녹화가 끝나면 MP4로 저장되고 <b>최근 녹화</b>에 나타납니다.</li>
</ol>
<h3>동영상 편집기</h3>
<p>위쪽 <b>✂ 편집기</b>(Ctrl+E)에서 녹화한 영상을 자르기·구간 제거·나누기·합치기·소리 추출·소리 제거·형식 변환할 수 있습니다.
시작점(<b>[</b>)과 끝점(<b>]</b>)을 찍고 <b>Enter</b>로 구간을 추가한 뒤 시작을 누르세요.</p>
<h3>단축키</h3>
<table cellpadding="4">
<tr><td><b>F9</b></td><td>녹화 시작 / 종료 (카운트다운 중에는 취소)</td></tr>
<tr><td><b>F10</b></td><td>일시정지 / 재개</td></tr>
<tr><td><b>Enter / Esc</b></td><td>영역 선택 확정 / 취소</td></tr>
<tr><td><b>방향키</b></td><td>영역 선택 중 1px 이동 (Shift: 10px)</td></tr>
</table>
<h3>소리</h3>
<p>오른쪽 패널에서 <b>시스템 소리</b>(PC에서 나는 소리)와 <b>마이크</b>를 각각 켜고 장치를 고릅니다.
음량 막대로 소리가 들어오는지 녹화 전에 확인할 수 있습니다. 두 소리는 저장할 때 하나로 합쳐집니다.
녹화 중 미니 컨트롤 바의 마이크 버튼으로 마이크만 잠시 음소거할 수 있습니다.</p>
<p>녹화 중 장치가 분리되면 자동으로 다시 연결하고, 끊긴 구간은 무음으로 채워 영상과 시간이 어긋나지 않게 합니다.</p>"""


class _ProbeBridge(QObject):
    done = Signal(object)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, palette_tokens: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setMinimumWidth(520)
        self._settings = settings
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.output = QLineEdit(settings.output_dir)
        browse = QPushButton(icons.icon("folder", palette_tokens.text), "")
        browse.setToolTip("저장 폴더 선택")
        browse.clicked.connect(self._browse_output)
        row = QHBoxLayout()
        row.addWidget(self.output, 1)
        row.addWidget(browse)
        form.addRow("저장 폴더", row)

        self.template = QLineEdit(settings.filename_template)
        self.template_preview = QLabel()
        self.template_preview.setProperty("role", "muted")
        self.template.textChanged.connect(self._update_template_preview)
        self._update_template_preview()
        form.addRow("파일 이름", self.template)
        form.addRow("", self.template_preview)

        self.fps = QComboBox()
        for fps in (15, 24, 30, 60):
            self.fps.addItem(f"{fps} FPS", fps)
        self.fps.setCurrentIndex(max(0, self.fps.findData(settings.profile.fps)))
        form.addRow("프레임", self.fps)

        self.quality = QComboBox()
        for key, label in QUALITY_LABELS.items():
            self.quality.addItem(label, key)
        self.quality.setCurrentIndex(max(0, self.quality.findData(settings.profile.quality_preset)))
        form.addRow("품질", self.quality)

        self.countdown = QSpinBox()
        self.countdown.setRange(0, 10)
        self.countdown.setSuffix(" 초")
        self.countdown.setValue(settings.countdown_s)
        form.addRow("카운트다운", self.countdown)

        self.minimize = QCheckBox("녹화를 시작하면 vcam 창을 최소화")
        self.minimize.setChecked(settings.minimize_on_record)
        form.addRow("", self.minimize)
        self.guide = QCheckBox("영역 모드에서 바탕화면에 가이드 프레임 표시")
        self.guide.setChecked(settings.show_guide_frame)
        form.addRow("", self.guide)
        self.auto_update = QCheckBox("새 버전을 자동으로 확인하고 내려받기 (GitHub)")
        self.auto_update.setToolTip("시작할 때 GitHub에 최신 버전만 물어봅니다. 화면·소리·파일 정보는 보내지 않습니다.")
        self.auto_update.setChecked(settings.auto_update)
        form.addRow("", self.auto_update)

        advanced = QLabel("고급 설정")
        advanced.setProperty("role", "section")
        form.addRow(advanced)

        self.encoder = QComboBox()
        for key, label in ENCODER_LABELS.items():
            self.encoder.addItem(label, key)
        self.encoder.setCurrentIndex(max(0, self.encoder.findData(settings.profile.encoder_preference)))
        form.addRow("인코더", self.encoder)

        self.backend = QComboBox()
        for key, label in (("auto", "자동 (DXcam → GDI)"), ("dxcam", "DXcam (고성능)"), ("mss", "GDI (호환성)")):
            self.backend.addItem(label, key)
        self.backend.setCurrentIndex(max(0, self.backend.findData(settings.capture_backend)))
        form.addRow("캡처 방식", self.backend)

        self.ffmpeg = QLineEdit(settings.ffmpeg_path)
        self.ffmpeg.setPlaceholderText("비워 두면 자동으로 찾습니다")
        ff_browse = QPushButton(icons.icon("folder", palette_tokens.text), "")
        ff_browse.clicked.connect(self._browse_ffmpeg)
        ff_row = QHBoxLayout()
        ff_row.addWidget(self.ffmpeg, 1)
        ff_row.addWidget(ff_browse)
        form.addRow("FFmpeg 위치", ff_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(buttons)

    def _update_template_preview(self) -> None:
        from datetime import datetime

        self.template_preview.setText("예: " + render_filename(self.template.text() or DEFAULT_FILENAME_TEMPLATE, datetime.now()) + ".mp4")

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.output.text())
        if path:
            self.output.setText(path)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg.exe 선택", "", "ffmpeg.exe (ffmpeg.exe)")
        if path:
            self.ffmpeg.setText(path)

    def _accept(self) -> None:
        if self.ffmpeg.text().strip() and find_ffmpeg(self.ffmpeg.text().strip()) is None:
            QMessageBox.warning(self, "FFmpeg", "지정한 위치에서 ffmpeg.exe와 ffprobe.exe를 함께 찾지 못했습니다.")
            return
        self.accept()

    def result_settings(self) -> Settings:
        if self.ffmpeg.text().strip() != self._settings.ffmpeg_path:
            clear_cache()
        profile = replace(
            self._settings.profile,
            fps=int(self.fps.currentData()),
            quality_preset=self.quality.currentData(),
            encoder_preference=self.encoder.currentData(),
        )
        return replace(
            self._settings,
            output_dir=self.output.text().strip() or self._settings.output_dir,
            filename_template=self.template.text().strip() or DEFAULT_FILENAME_TEMPLATE,
            ffmpeg_path=self.ffmpeg.text().strip(),
            capture_backend=self.backend.currentData(),
            countdown_s=self.countdown.value(),
            minimize_on_record=self.minimize.isChecked(),
            show_guide_frame=self.guide.isChecked(),
            auto_update=self.auto_update.isChecked(),
            profile=profile,
        )


class DiagnosticsDialog(QDialog):
    report_ready = Signal(object)

    def __init__(self, settings: Settings, palette_tokens: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("시스템 진단")
        self.resize(640, 520)
        self.p = palette_tokens
        self._settings = settings
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["항목", "상태"])
        self.tree.setColumnWidth(0, 200)
        self.tree.setIconSize(QSize(16, 16))
        self.status = QLabel("진단 중… (인코더 시험에 몇 초 걸릴 수 있습니다)")
        self.status.setProperty("role", "muted")
        self.rerun = QPushButton(icons.icon("refresh", palette_tokens.text), "다시 진단")
        self.rerun.clicked.connect(self.run)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.rerun)
        bottom.addWidget(close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.addWidget(self.tree, 1)
        layout.addLayout(bottom)
        self._bridge = _ProbeBridge(self)
        self._bridge.done.connect(self._on_done)
        self.run()

    def run(self) -> None:
        self.tree.clear()
        self.rerun.setEnabled(False)
        self.status.setText("진단 중… (인코더 시험에 몇 초 걸릴 수 있습니다)")
        s = self._settings
        threading.Thread(
            target=lambda: self._bridge.done.emit(run_probe(s.ffmpeg_path, Path(s.output_dir), s.profile.encoder_preference)),
            name="vcam-probe",
            daemon=True,
        ).start()

    @Slot(object)
    def _on_done(self, report: ProbeReport) -> None:
        self.rerun.setEnabled(True)
        groups: dict[str, QTreeWidgetItem] = {}
        for item in report.items:
            parent = groups.get(item.group)
            if parent is None:
                parent = QTreeWidgetItem([item.group, ""])
                parent.setFirstColumnSpanned(False)
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                groups[item.group] = parent
            row = QTreeWidgetItem([item.name, item.detail + (f"\n→ {item.hint}" if item.hint else "")])
            name, color = {True: ("check", self.p.ok), False: ("alert", self.p.rec), None: ("info", self.p.muted)}[item.ok]
            row.setIcon(0, icons.icon(name, color))
            parent.addChild(row)
        self.status.setText("녹화할 준비가 되었습니다." if report.can_record else "녹화하려면 위의 경고 항목을 해결해 주세요.")
        self.report_ready.emit(report)


class RecoveryDialog(QDialog):
    def __init__(self, sessions: list[IncompleteSession], settings: Settings, palette_tokens: Palette,
                 parent: QWidget | None = None) -> None:  # fmt: skip
        super().__init__(parent)
        self.setWindowTitle("미완료 녹화 복구")
        self.resize(560, 360)
        self._settings = settings
        self.recovered: list[Path] = []
        title = QLabel("이전에 정상적으로 끝나지 않은 녹화가 있습니다")
        title.setProperty("role", "section")
        desc = QLabel("복구하면 기록된 부분까지 MP4로 저장합니다. 버리기를 누르면 휴지통으로 옮깁니다.")
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["시작 시각", "크기"])
        self.tree.setColumnWidth(0, 300)
        for s in sessions:
            item = QTreeWidgetItem([s.started_label, format_bytes(s.size_bytes)])
            item.setData(0, Qt.ItemDataRole.UserRole, s)
            item.setIcon(0, icons.icon("film", palette_tokens.muted))
            self.tree.addTopLevelItem(item)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        recover = QPushButton(icons.icon("lifebuoy", palette_tokens.accent_text), "복구")
        recover.setProperty("role", "primary")
        recover.clicked.connect(self._recover)
        discard = QPushButton(icons.icon("trash", palette_tokens.text), "버리기")
        discard.clicked.connect(self._discard)
        later = QPushButton("나중에")
        later.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(discard)
        buttons.addWidget(later)
        buttons.addWidget(recover)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

    def _current(self) -> tuple[QTreeWidgetItem, IncompleteSession] | None:
        item = self.tree.currentItem()
        return (item, item.data(0, Qt.ItemDataRole.UserRole)) if item else None

    def _recover(self) -> None:
        cur = self._current()
        if cur is None:
            return
        item, session = cur
        ffmpeg = find_ffmpeg(self._settings.ffmpeg_path)
        if ffmpeg is None:
            QMessageBox.warning(self, "복구", "FFmpeg를 찾을 수 없어 복구할 수 없습니다. 설정에서 위치를 지정해 주세요.")
            return
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            from datetime import datetime

            info = recover_session(session, ffmpeg, Path(self._settings.output_dir),
                                   render_filename(self._settings.filename_template, datetime.now()))  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - 사용자에게 원인을 보여준다
            QMessageBox.warning(self, "복구 실패", f"복구하지 못했습니다.\n{exc}")
            return
        finally:
            self.unsetCursor()
        self.recovered.append(info.path)
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        QMessageBox.information(self, "복구 완료", f"{info.path.name} 으로 저장했습니다. ({info.duration_s:.1f}초)")
        if self.tree.topLevelItemCount() == 0:
            self.accept()

    def _discard(self) -> None:
        cur = self._current()
        if cur is None:
            return
        item, session = cur
        if QMessageBox.question(self, "버리기", "이 미완료 녹화를 휴지통으로 옮길까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            discard_session(session)
        except OSError as exc:
            QMessageBox.warning(self, "버리기 실패", str(exc))
            return
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        if self.tree.topLevelItemCount() == 0:
            self.accept()


class AboutDialog(QDialog):
    def __init__(self, palette_tokens: Palette, version: str, parent: QWidget | None = None, help_mode: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("사용 안내" if help_mode else "vCAM 정보")
        self.setMinimumWidth(600)
        art = QLabel()
        pm = QPixmap(str(ASSETS / "brand" / "vaveling_glasses.jpg"))
        if not pm.isNull():
            art.setPixmap(pm.scaledToHeight(300, Qt.TransformationMode.SmoothTransformation))
        art.setAlignment(Qt.AlignmentFlag.AlignTop)
        title = QLabel("vCAM")
        title.setProperty("role", "title")
        sub = QLabel(f"버전 {version}  ·  로컬 우선 Windows 화면 녹화")
        sub.setProperty("role", "muted")
        body = QLabel(HELP_TEXT if help_mode else ABOUT_TEXT)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        text = QVBoxLayout()
        text.addWidget(title)
        text.addWidget(sub)
        text.addSpacing(8)
        text.addWidget(body, 1)
        row = QHBoxLayout()
        row.addWidget(art)
        row.addSpacing(16)
        row.addLayout(text, 1)
        body.setOpenExternalLinks(True)
        ok = QPushButton("확인")
        ok.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if not help_mode:
            visit = QPushButton("vaveling.app 방문하기")
            visit.setProperty("role", "primary")
            visit.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(VAVELING_URL)))
            buttons.addWidget(visit)
        else:
            ok.setProperty("role", "primary")
        buttons.addWidget(ok)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.addLayout(row)
        layout.addLayout(buttons)


def show_consent_notice(parent: QWidget) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("녹화 전에 확인해 주세요")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("녹화 동의 안내")
    box.setInformativeText(CONSENT_TEXT)
    box.addButton("확인했습니다", QMessageBox.ButtonRole.AcceptRole)
    box.exec()
