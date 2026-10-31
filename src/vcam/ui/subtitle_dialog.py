"""자막 만들기(SRT) 대화상자: 옵션 → 진행률 → 결과. 무거운 작업은 작업 스레드에서 돌린다."""

from __future__ import annotations

import logging
import threading
import weakref
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.services.profile_service import Settings
from vcam.subtitles import languages
from vcam.subtitles import transcriber as tr
from vcam.subtitles.pipeline import (
    STAGE_LABELS,
    EngineCache,
    SubtitleOptions,
    SubtitleResult,
    run_job,
)
from vcam.subtitles.translator import find_offline_model
from vcam.ui import icons
from vcam.ui.srt_viewer import SrtViewerDialog
from vcam.ui.tokens import Palette
from vcam.ui.widgets.recent_recordings import reveal_in_explorer

log = logging.getLogger(__name__)

DEVICE_LABELS = (("auto", "자동 (GPU가 있으면 GPU)"), ("cuda", "GPU (NVIDIA CUDA)"), ("cpu", "CPU"))
MAX_CHARS_CHOICES = (30, 40, 45, 60)
SUBTITLE_SETTING_KEYS = (
    "subtitle_language", "subtitle_model", "subtitle_device", "subtitle_download", "subtitle_translate",
    "subtitle_targets", "subtitle_online", "subtitle_model_dir", "subtitle_max_chars",
)  # fmt: skip


class _Bridge(QObject):
    """작업 스레드 → GUI 스레드. 모든 신호는 큐 연결로 GUI 스레드에서 처리된다."""

    file_started = Signal(int, object)
    file_done = Signal(int, object)
    file_failed = Signal(int, str)
    progress = Signal(int, str, float)
    log = Signal(str)
    finished = Signal(int, int)
    environment = Signal(bool, object)  # cuda_available, offline model (Path, kind) | None


class SubtitleDialog(QDialog):
    settings_changed = Signal(dict)  # 저장할 Settings 필드 변경분

    def __init__(
        self,
        paths: list[Path],
        settings: Settings,
        palette_tokens: Palette,
        ffmpeg_lookup: Callable[[], FfmpegPaths | None],
        cache: EngineCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("자막 만들기 (SRT)")
        self.setMinimumWidth(640)
        self.p = palette_tokens
        self.paths = [Path(p) for p in paths]
        self._ffmpeg_lookup = ffmpeg_lookup
        self._cache = cache
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._env_thread: threading.Thread | None = None
        self._results: list[SubtitleResult] = []
        self._done = 0
        self._bridge = _Bridge(self)
        self._bridge.file_started.connect(self._on_file_started)
        self._bridge.file_done.connect(self._on_file_done)
        self._bridge.file_failed.connect(self._on_file_failed)
        self._bridge.progress.connect(self._on_progress)
        self._bridge.log.connect(self._append_log)
        self._bridge.finished.connect(self._on_finished)
        self._bridge.environment.connect(self._on_environment)
        self._build(settings)
        self._probe_environment()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build(self, s: Settings) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        names = [p.name for p in self.paths]
        shown = "\n".join(names[:4]) + (f"\n… 외 {len(names) - 4}개" if len(names) > 4 else "")
        header = QLabel(f"<b>자막을 만들 영상</b> ({len(names)}개)<br>{shown.replace(chr(10), '<br>')}")
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.cmb_language = QComboBox()
        self.cmb_language.addItem("자동 감지 (권장)", languages.AUTO)
        for lang in languages.LANGUAGES:
            self.cmb_language.addItem(lang.name_ko, lang.code)
        self._select_data(self.cmb_language, s.subtitle_language)
        self.cmb_language.setAccessibleName("음성 언어")
        form.addRow("음성 언어", self.cmb_language)

        self.cmb_model = QComboBox()
        self.cmb_model.setAccessibleName("음성 인식 모델")
        self._fill_models(s.subtitle_model)
        self.cmb_model.currentIndexChanged.connect(self._update_model_notice)
        form.addRow("인식 모델", self.cmb_model)
        self.model_notice = QLabel("")
        self.model_notice.setWordWrap(True)
        self.model_notice.setProperty("role", "muted")
        form.addRow("", self.model_notice)

        self.chk_download = QCheckBox("모델이 없으면 내려받기 (HuggingFace, 인터넷 사용)")
        self.chk_download.setChecked(s.subtitle_download)
        self.chk_download.toggled.connect(self._update_model_notice)
        form.addRow("", self.chk_download)

        self.cmb_device = QComboBox()
        for key, label in DEVICE_LABELS:
            self.cmb_device.addItem(label, key)
        self._select_data(self.cmb_device, s.subtitle_device)
        self.cmb_device.setAccessibleName("처리 장치")
        self.device_notice = QLabel("GPU 확인 중…")
        self.device_notice.setProperty("role", "muted")
        device_row = QHBoxLayout()
        device_row.addWidget(self.cmb_device, 1)
        device_row.addWidget(self.device_notice)
        form.addRow("처리 장치", device_row)

        self.cmb_max_chars = QComboBox()
        for n in MAX_CHARS_CHOICES:
            self.cmb_max_chars.addItem(f"{n}자", n)
        self._select_data(self.cmb_max_chars, s.subtitle_max_chars)
        self.cmb_max_chars.setAccessibleName("자막 한 줄 최대 글자 수")
        form.addRow("한 줄 최대", self.cmb_max_chars)
        layout.addLayout(form)

        # 번역
        self.grp_translate = QGroupBox("번역 자막도 함께 만들기")
        self.grp_translate.setCheckable(True)
        self.grp_translate.setChecked(s.subtitle_translate)
        tl = QVBoxLayout(self.grp_translate)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.target_boxes: dict[str, QCheckBox] = {}
        for i, lang in enumerate(languages.LANGUAGES):
            box = QCheckBox(lang.name_ko)
            box.setChecked(lang.code in s.subtitle_targets)
            self.target_boxes[lang.code] = box
            grid.addWidget(box, i // 4, i % 4)
        tl.addLayout(grid)
        model_row = QHBoxLayout()
        self.edt_model_dir = QLineEdit(s.subtitle_model_dir)
        self.edt_model_dir.setPlaceholderText("오프라인 번역 모델 폴더 (M2M100, 비우면 자동 탐색)")
        self.edt_model_dir.setAccessibleName("오프라인 번역 모델 폴더")
        self.edt_model_dir.editingFinished.connect(self._probe_environment)
        browse = QPushButton(icons.icon("folder", self.p.text), "")
        browse.setToolTip("번역 모델 폴더 선택")
        browse.setAccessibleName("번역 모델 폴더 선택")
        browse.clicked.connect(self._browse_model_dir)
        model_row.addWidget(self.edt_model_dir, 1)
        model_row.addWidget(browse)
        tl.addLayout(model_row)
        self.offline_notice = QLabel("오프라인 번역 모델 확인 중…")
        self.offline_notice.setWordWrap(True)
        self.offline_notice.setProperty("role", "muted")
        tl.addWidget(self.offline_notice)
        self.chk_online = QCheckBox("온라인 번역 허용 (Google · MyMemory)")
        self.chk_online.setChecked(s.subtitle_online)
        self.chk_online.setToolTip("켜면 오프라인 모델이 없거나 실패할 때 자막 본문을 외부 번역 서비스로 보냅니다.")
        tl.addWidget(self.chk_online)
        online_note = QLabel("온라인 번역을 켜면 자막 본문이 해당 서비스로 전송됩니다. 기본값은 PC 안에서만 처리합니다.")
        online_note.setWordWrap(True)
        online_note.setProperty("role", "muted")
        tl.addWidget(online_note)
        layout.addWidget(self.grp_translate)

        save_note = QLabel("저장 위치: 영상과 같은 폴더, 파일 이름 <b>영상이름.언어.srt</b> (기존 파일은 덮어쓰지 않습니다)")
        save_note.setProperty("role", "muted")
        save_note.setWordWrap(True)
        layout.addWidget(save_note)

        # 진행
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("자막 생성 진행률")
        self.status = QLabel("시작을 누르면 자막을 만듭니다.")
        self.status.setProperty("role", "muted")
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFixedHeight(110)
        self.log_view.setAccessibleName("진행 기록")
        layout.addWidget(self.log_view)

        # 버튼
        buttons = QHBoxLayout()
        self.btn_view = QPushButton(icons.icon("subtitles", self.p.text), "자막 보기")
        self.btn_view.clicked.connect(self._view_results)
        self.btn_view.setEnabled(False)
        self.btn_reveal = QPushButton(icons.icon("folder", self.p.text), "폴더에서 보기")
        self.btn_reveal.clicked.connect(self._reveal_results)
        self.btn_reveal.setEnabled(False)
        self.btn_start = QPushButton(icons.icon("play", self.p.accent_text), "시작")
        self.btn_start.setProperty("role", "primary")
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.start)
        self.btn_close = QPushButton("닫기")
        self.btn_close.clicked.connect(self._close_or_cancel)
        buttons.addWidget(self.btn_view)
        buttons.addWidget(self.btn_reveal)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self._option_widgets: list[QWidget] = [
            self.cmb_language, self.cmb_model, self.chk_download, self.cmb_device, self.cmb_max_chars, self.grp_translate,
        ]  # fmt: skip
        self._update_model_notice()

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _fill_models(self, selected: str) -> None:
        cached = set(tr.cached_models())
        auto = tr.default_model()
        self.cmb_model.addItem(f"자동 — 받아 둔 모델 중 가장 정확한 것 ({auto})", "")
        for name, (_repo, desc, gb) in tr.MODELS.items():
            mark = "  ✓ 받아 둠" if name in cached else ""
            self.cmb_model.addItem(f"{name} · {desc} · 약 {gb:g} GB{mark}", name)
        self._select_data(self.cmb_model, selected)

    def _update_model_notice(self) -> None:
        name = self.cmb_model.currentData() or tr.default_model()
        if tr.is_model_cached(name):
            self.model_notice.setText(f"'{name}' 모델이 PC에 있습니다.")
        elif self.chk_download.isChecked():
            self.model_notice.setText(f"'{name}' 모델(약 {tr.model_size_gb(name):g} GB)을 처음 한 번 내려받습니다.")
        else:
            self.model_notice.setText(
                f"'{name}' 모델이 PC에 없습니다. 아래 '모델이 없으면 내려받기'를 켜거나 받아 둔 모델을 고르세요."
            )

    def _browse_model_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "오프라인 번역 모델 폴더 선택", self.edt_model_dir.text())
        if folder:
            self.edt_model_dir.setText(folder)
            self._probe_environment()

    def _probe_environment(self) -> None:
        """GPU와 오프라인 번역 모델 확인은 라이브러리 로드가 필요해 작업 스레드에서 한다.

        스레드는 대화상자를 강하게 붙잡지 않는다. 확인이 끝나기 전에 창이 닫혀 지워졌으면
        결과를 버린다 (지워진 QObject로 신호를 보내면 프로세스가 죽을 수 있다).
        """
        model_dir = self.edt_model_dir.text().strip() or None
        bridge_ref = weakref.ref(self._bridge)

        def work() -> None:
            try:
                cuda = tr.cuda_available()
            except Exception:  # noqa: BLE001 - 확인 실패는 '없음'으로 표시
                cuda = False
            found = find_offline_model(model_dir)
            bridge = bridge_ref()
            if bridge is None or not shiboken6.isValid(bridge):
                log.debug("환경 확인이 끝나기 전에 자막 창이 닫혀 결과를 버립니다")
                return
            bridge.environment.emit(cuda, found)

        self._env_thread = threading.Thread(target=work, name="vcam-subtitle-env", daemon=True)
        self._env_thread.start()

    @Slot(bool, object)
    def _on_environment(self, cuda: bool, found) -> None:
        self.device_notice.setText("GPU 사용 가능" if cuda else "GPU 없음 → CPU")
        if found:
            folder, kind = found
            self.offline_notice.setText(f"오프라인 번역 모델 찾음: {'M2M100' if kind == 'm2m100' else 'NLLB'} ({folder})")
        else:
            self.offline_notice.setText(
                "오프라인 번역 모델이 없습니다. m2m100_418M-ct2-int8 폴더를 지정하거나 "
                "%LOCALAPPDATA%\\vcam\\models 안에 넣어 주세요. 없으면 온라인 번역을 허용해야 번역됩니다."
            )

    # ── 실행 ──────────────────────────────────────────────────────────────
    def options(self) -> SubtitleOptions:
        targets = tuple(code for code, box in self.target_boxes.items() if box.isChecked())
        return SubtitleOptions(
            model=self.cmb_model.currentData() or "",
            device=self.cmb_device.currentData(),
            source_language=self.cmb_language.currentData(),
            allow_download=self.chk_download.isChecked(),
            translate=self.grp_translate.isChecked() and bool(targets),
            targets=targets or ("ko",),
            allow_online=self.chk_online.isChecked(),
            model_dir=self.edt_model_dir.text().strip(),
            max_chars=int(self.cmb_max_chars.currentData()),
        )

    def _settings_changes(self, o: SubtitleOptions) -> dict:
        return {
            "subtitle_language": o.source_language,
            "subtitle_model": o.model,
            "subtitle_device": o.device,
            "subtitle_download": o.allow_download,
            "subtitle_translate": self.grp_translate.isChecked(),
            "subtitle_targets": o.targets,
            "subtitle_online": o.allow_online,
            "subtitle_model_dir": o.model_dir,
            "subtitle_max_chars": o.max_chars,
        }

    @property
    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_busy:
            return
        ffmpeg = self._ffmpeg_lookup()
        if ffmpeg is None:
            QMessageBox.warning(self, "자막 만들기", "FFmpeg를 찾을 수 없습니다. 설정에서 FFmpeg 경로를 지정하거나 설치해 주세요.")
            return
        options = self.options()
        if not options.allow_download and not tr.is_model_cached(options.resolved_model):
            QMessageBox.information(
                self, "자막 만들기",
                f"음성 인식 모델 '{options.resolved_model}'이(가) PC에 없습니다.\n"
                "'모델이 없으면 내려받기'를 켜거나 받아 둔 모델을 선택해 주세요.",
            )  # fmt: skip
            return
        if self.grp_translate.isChecked() and not any(b.isChecked() for b in self.target_boxes.values()):
            QMessageBox.information(self, "자막 만들기", "번역할 언어를 하나 이상 선택해 주세요.")
            return
        self.settings_changed.emit(self._settings_changes(options))
        log.info("자막 만들기 시작: 파일 %d개, 모델 %s, 장치 %s, 번역 %s",
                 len(self.paths), options.resolved_model, options.device, options.targets if options.translate else "없음")  # fmt: skip
        self._results = []
        self._done = 0
        self._cancel.clear()
        self.log_view.clear()
        self.progress.setValue(0)
        self.btn_view.setEnabled(False)
        self.btn_reveal.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_close.setText("취소")
        for w in self._option_widgets:
            w.setEnabled(False)
        self.status.setText(STAGE_LABELS["load"] + "…")
        b = self._bridge
        paths = list(self.paths)
        cache = self._cache
        cancel = self._cancel

        def work() -> None:
            try:
                success, failure = run_job(
                    paths, options, ffmpeg, cache,
                    on_file_start=b.file_started.emit, on_file_done=b.file_done.emit,
                    on_file_failed=b.file_failed.emit, progress=b.progress.emit,
                    cancel=cancel.is_set, log_fn=b.log.emit,
                )  # fmt: skip
            except Exception as error:  # noqa: BLE001 - 스레드가 조용히 죽지 않게 한다
                log.error("자막 작업 스레드 오류", exc_info=True)
                b.log.emit(f"오류: {error}")
                success, failure = 0, len(paths)
            b.finished.emit(success, failure)

        self._thread = threading.Thread(target=work, name="vcam-subtitles", daemon=True)
        self._thread.start()

    @Slot(int, object)
    def _on_file_started(self, index: int, path: Path) -> None:
        self.status.setText(f"[{index + 1}/{len(self.paths)}] {Path(path).name} — 소리 읽는 중…")

    @Slot(int, str, float)
    def _on_progress(self, index: int, stage: str, fraction: float) -> None:
        label = STAGE_LABELS.get(stage, stage)
        if index < 0:
            self.status.setText(label + "…")
            return
        weight = {"decode": (0.0, 0.05), "transcribe": (0.05, 0.75), "translate": (0.8, 0.2)}.get(stage, (0.0, 1.0))
        within = weight[0] + weight[1] * fraction
        total = (self._done + within) / max(len(self.paths), 1)
        self.progress.setValue(int(total * 1000))
        self.status.setText(f"[{index + 1}/{len(self.paths)}] {self.paths[index].name} — {label} {int(fraction * 100)}%")

    @Slot(int, object)
    def _on_file_done(self, index: int, result: SubtitleResult) -> None:
        self._done += 1
        self._results.append(result)
        self.progress.setValue(int(self._done / max(len(self.paths), 1) * 1000))

    @Slot(int, str)
    def _on_file_failed(self, index: int, message: str) -> None:
        self._done += 1
        self._append_log(f"✖ {self.paths[index].name}: {message}")
        self.progress.setValue(int(self._done / max(len(self.paths), 1) * 1000))

    @Slot(str)
    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @Slot(int, int)
    def _on_finished(self, success: int, failure: int) -> None:
        self._thread = None
        self.btn_start.setEnabled(True)
        self.btn_close.setText("닫기")
        for w in self._option_widgets:
            w.setEnabled(True)
        files = [p for r in self._results for p in r.files]
        self.btn_view.setEnabled(bool(files))
        self.btn_reveal.setEnabled(bool(files))
        partial = [e for r in self._results for e in r.errors]
        if self._cancel.is_set():
            self.status.setText(f"취소되었습니다. (완료 {success}개)")
        elif failure == 0 and not partial:
            self.status.setText(f"완료: 자막 {len(files)}개를 저장했습니다.")
        elif success:
            self.status.setText(f"일부 완료: 성공 {success}개 · 실패 {failure}개 · 번역 오류 {len(partial)}건 — 아래 기록을 확인해 주세요.")
        else:
            self.status.setText("실패했습니다. 아래 기록을 확인해 주세요.")
        self.progress.setValue(1000 if not self._cancel.is_set() else self.progress.value())

    # ── 결과 ──────────────────────────────────────────────────────────────
    def _result_files(self) -> list[Path]:
        return [p for r in self._results for p in r.files if p.exists()]

    def _view_results(self) -> None:
        files = self._result_files()
        if not files:
            return
        # 영상 하나의 자막들(원문+번역)을 나란히 보여 준다. 여러 영상이면 첫 영상 기준.
        first = self._results[0].files if self._results else files
        viewer = SrtViewerDialog(first, self.p, self)
        viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        viewer.show()

    def _reveal_results(self) -> None:
        files = self._result_files()
        if files:
            reveal_in_explorer(files[0])

    # ── 닫기/취소 ─────────────────────────────────────────────────────────
    def cancel(self) -> None:
        """진행 중인 작업에 취소를 요청한다 (다음 구간 경계에서 멈춘다)."""
        self._cancel.set()

    def _close_or_cancel(self) -> None:
        if self.is_busy:
            self._cancel.set()
            self.status.setText("취소 중… (지금 처리 중인 구간이 끝나면 멈춥니다)")
            self.btn_close.setEnabled(False)
            self._bridge.finished.connect(lambda *_: self.btn_close.setEnabled(True))
            return
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.is_busy:
            answer = QMessageBox.question(self, "자막 만들기", "자막을 만드는 중입니다. 작업을 취소하고 닫을까요?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancel.set()
        super().closeEvent(event)


def settings_to_options(settings: Settings) -> SubtitleOptions:
    """설정에 저장된 마지막 선택값을 옵션으로 바꾼다 (테스트·CLI용)."""
    d = asdict(settings)
    return SubtitleOptions(
        model=d["subtitle_model"], device=d["subtitle_device"], source_language=d["subtitle_language"],
        allow_download=d["subtitle_download"], translate=d["subtitle_translate"], targets=tuple(d["subtitle_targets"]),
        allow_online=d["subtitle_online"], model_dir=d["subtitle_model_dir"], max_chars=d["subtitle_max_chars"],
    )  # fmt: skip
