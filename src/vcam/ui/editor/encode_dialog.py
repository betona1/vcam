"""인코딩 설정 대화상자: 형식·코덱·해상도·FPS·품질(VBR/CBR)·디인터레이스·소리·속도·회전·프리셋."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vcam.editing.formats import (
    AUDIO_CODECS,
    CONTAINERS,
    FPS_CHOICES,
    RESOLUTION_PRESETS,
    SAMPLE_RATES,
    VIDEO_CODECS,
    EncodeSettings,
)
from vcam.editing.storage import (
    BUILTIN_PRESETS,
    delete_user_preset,
    load_user_presets,
    save_user_preset,
)


def summarize(s: EncodeSettings) -> str:
    s = s.fixed()
    parts = [CONTAINERS[s.container].label.split(" ")[0], VIDEO_CODECS[s.video_codec].label.split(" (")[0]]
    if s.resolution == "original":
        parts.append("원본 크기")
    elif s.resolution == "fit_width":
        parts.append(f"가로 {s.width}")
    elif s.resolution == "fit_height":
        parts.append(f"세로 {s.height}")
    else:
        parts.append(f"{s.width}×{s.height}")
    parts.append(f"{s.fps}fps" if s.fps else "원본 fps")
    parts.append(f"품질 {s.quality}%" if s.rate_control == "vbr" else f"{s.bitrate_kbps / 1000:g}Mbps")
    if CONTAINERS[s.container].audio:
        parts.append(AUDIO_CODECS[s.audio_codec].label.split(" (")[0])
    if s.speed != 1.0:
        parts.append(f"{s.speed:g}배속")
    if s.rotate or s.flip_h or s.flip_v:
        parts.append("회전/뒤집기")
    return " · ".join(parts)


class EncodeDialog(QDialog):
    def __init__(self, settings: EncodeSettings, video_ok: set[str], audio_ok: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("인코딩 설정")
        self.setMinimumWidth(560)
        self._video_ok, self._audio_ok = video_ok, audio_ok

        # 프리셋
        self.preset = QComboBox()
        self.preset.setMinimumWidth(260)
        self._reload_presets()
        self.preset.activated.connect(self._apply_preset)
        save_btn = QPushButton("현재 설정 저장…")
        save_btn.clicked.connect(self._save_preset)
        del_btn = QPushButton("삭제")
        del_btn.clicked.connect(self._delete_preset)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("프리셋"))
        prow.addWidget(self.preset, 1)
        prow.addWidget(save_btn)
        prow.addWidget(del_btn)

        # 영상
        self.container = QComboBox()
        for c in CONTAINERS.values():
            # 영상 코덱과(소리를 담는 형식이면) 소리 코덱이 하나라도 있어야 고를 수 있다
            if any(v in video_ok for v in c.video) and (not c.audio or any(a in audio_ok for a in c.audio)):
                self.container.addItem(f"{c.label}  ({c.ext})", c.key)
        self.container.currentIndexChanged.connect(self._on_container)
        self.vcodec = QComboBox()
        self.res_mode = QComboBox()
        for key, label in (("original", "원본 크기 유지"), ("preset", "프리셋 크기"), ("fit_width", "가로 맞춤(비율 유지)"),
                           ("fit_height", "세로 맞춤(비율 유지)"), ("custom", "직접 입력")):  # fmt: skip
            self.res_mode.addItem(label, key)
        self.res_preset = QComboBox()
        for label, w, h in RESOLUTION_PRESETS:
            self.res_preset.addItem(label, (w, h))
        self.res_w, self.res_h = QSpinBox(), QSpinBox()
        for sp in (self.res_w, self.res_h):
            sp.setRange(16, 7680)
            sp.setSingleStep(2)
        self.res_mode.currentIndexChanged.connect(self._sync_res)
        self.res_preset.currentIndexChanged.connect(self._preset_to_spins)
        res_row = QHBoxLayout()
        res_row.addWidget(self.res_mode)
        res_row.addWidget(self.res_preset)
        res_row.addWidget(self.res_w)
        self.res_x = QLabel("×")
        res_row.addWidget(self.res_x)
        res_row.addWidget(self.res_h)
        self.fps = QComboBox()
        for f in FPS_CHOICES:
            self.fps.addItem("원본" if f == 0 else f"{f} FPS", f)
        self.vbr = QRadioButton("품질 기준 (VBR)")
        self.cbr = QRadioButton("비트레이트 고정 (CBR)")
        grp = QButtonGroup(self)
        grp.addButton(self.vbr)
        grp.addButton(self.cbr)
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(0, 100)
        self.quality_label = QLabel()
        self.quality.valueChanged.connect(lambda v: self.quality_label.setText(f"{v}%"))
        self.bitrate = QDoubleSpinBox()
        self.bitrate.setRange(0.2, 100)
        self.bitrate.setSuffix(" Mbps")
        self.bitrate.setDecimals(1)
        self.vbr.toggled.connect(self._sync_rate)
        q_row = QHBoxLayout()
        q_row.addWidget(self.vbr)
        q_row.addWidget(self.quality, 1)
        q_row.addWidget(self.quality_label)
        b_row = QHBoxLayout()
        b_row.addWidget(self.cbr)
        b_row.addWidget(self.bitrate)
        b_row.addStretch(1)
        self.deint = QComboBox()
        for key, label in (("auto", "자동"), ("always", "항상"), ("off", "사용 안 함")):
            self.deint.addItem(label, key)
        vbox = QFormLayout()
        vbox.addRow("형식", self.container)
        vbox.addRow("영상 코덱", self.vcodec)
        vbox.addRow("크기", res_row)
        vbox.addRow("프레임", self.fps)
        vbox.addRow("화질", q_row)
        vbox.addRow("", b_row)
        vbox.addRow("디인터레이스", self.deint)
        vgroup = QGroupBox("영상")
        vgroup.setLayout(vbox)

        # 소리
        self.acodec = QComboBox()
        self.abitrate = QComboBox()
        for kb in (96, 128, 160, 192, 256, 320):
            self.abitrate.addItem(f"{kb} kbps", kb)
        self.channels = QComboBox()
        for v, label in ((0, "원본"), (2, "스테레오"), (1, "모노")):
            self.channels.addItem(label, v)
        self.rate = QComboBox()
        for r in SAMPLE_RATES:
            self.rate.addItem("원본" if r == 0 else f"{r:,} Hz", r)
        self.normalize = QCheckBox("음량 자동 평준화 (작은 소리는 키우고 큰 소리는 줄임)")
        abox = QFormLayout()
        abox.addRow("소리 코덱", self.acodec)
        abox.addRow("비트레이트", self.abitrate)
        abox.addRow("채널", self.channels)
        abox.addRow("주파수", self.rate)
        abox.addRow("", self.normalize)
        self.agroup = QGroupBox("소리")
        self.agroup.setLayout(abox)

        # 효과
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 4.0)
        self.speed.setSingleStep(0.25)
        self.speed.setSuffix(" 배")
        self.rotate = QComboBox()
        for deg, label in ((0, "회전 안 함"), (90, "오른쪽으로 90°"), (180, "180°"), (270, "왼쪽으로 90°")):
            self.rotate.addItem(label, deg)
        self.flip_h = QCheckBox("좌우 뒤집기")
        self.flip_v = QCheckBox("상하 뒤집기")
        e_row = QHBoxLayout()
        e_row.addWidget(self.rotate)
        e_row.addWidget(self.flip_h)
        e_row.addWidget(self.flip_v)
        e_row.addStretch(1)
        ebox = QFormLayout()
        ebox.addRow("재생 속도", self.speed)
        ebox.addRow("회전", e_row)
        egroup = QGroupBox("효과")
        egroup.setLayout(ebox)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("role", "primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.addLayout(prow)
        layout.addWidget(vgroup)
        layout.addWidget(self.agroup)
        layout.addWidget(egroup)
        layout.addWidget(buttons)
        self.load(settings)

    def _accept(self) -> None:
        if self.vcodec.currentData() is None or (self.agroup.isEnabled() and self.acodec.currentData() is None):
            QMessageBox.warning(self, "인코딩 설정", "이 형식에 쓸 수 있는 코덱이 설치된 FFmpeg에 없습니다. 다른 형식을 골라 주세요.")
            return
        self.accept()

    # ── 값 넣기/빼기 ──────────────────────────────────────────────────────
    def load(self, s: EncodeSettings) -> None:
        s = s.fixed()
        self._set(self.container, s.container)
        self._on_container()
        self._set(self.vcodec, s.video_codec)
        self._set(self.acodec, s.audio_codec)
        self._set(self.res_mode, s.resolution)
        idx = next((i for i, (_l, w, h) in enumerate(RESOLUTION_PRESETS) if (w, h) == (s.width, s.height)), 2)
        self.res_preset.setCurrentIndex(idx)
        self.res_w.setValue(s.width)
        self.res_h.setValue(s.height)
        self._set(self.fps, s.fps)
        (self.vbr if s.rate_control == "vbr" else self.cbr).setChecked(True)
        self.quality.setValue(s.quality)
        self.quality_label.setText(f"{s.quality}%")
        self.bitrate.setValue(s.bitrate_kbps / 1000)
        self._set(self.deint, s.deinterlace)
        self._set(self.abitrate, s.audio_bitrate_kbps)
        self._set(self.channels, s.audio_channels)
        self._set(self.rate, s.sample_rate)
        self.normalize.setChecked(s.normalize)
        self.speed.setValue(s.speed)
        self._set(self.rotate, s.rotate)
        self.flip_h.setChecked(s.flip_h)
        self.flip_v.setChecked(s.flip_v)
        self._sync_res()
        self._sync_rate()

    def result_settings(self) -> EncodeSettings:
        return EncodeSettings(
            container=self.container.currentData(), video_codec=self.vcodec.currentData(),
            resolution=self.res_mode.currentData(), width=self.res_w.value(), height=self.res_h.value(),
            fps=self.fps.currentData(), rate_control="vbr" if self.vbr.isChecked() else "cbr",
            quality=self.quality.value(), bitrate_kbps=int(self.bitrate.value() * 1000),
            deinterlace=self.deint.currentData(), audio_codec=self.acodec.currentData() or "aac",
            audio_bitrate_kbps=self.abitrate.currentData(), audio_channels=self.channels.currentData(),
            sample_rate=self.rate.currentData(), normalize=self.normalize.isChecked(), speed=self.speed.value(),
            rotate=self.rotate.currentData(), flip_h=self.flip_h.isChecked(), flip_v=self.flip_v.isChecked(),
        ).fixed()  # fmt: skip

    @staticmethod
    def _set(combo: QComboBox, value) -> None:
        i = combo.findData(value)
        if i >= 0:
            combo.setCurrentIndex(i)

    def _on_container(self) -> None:
        c = CONTAINERS[self.container.currentData()]
        cur_v, cur_a = self.vcodec.currentData(), self.acodec.currentData()
        self.vcodec.clear()
        for key in c.video:
            if key in self._video_ok:
                self.vcodec.addItem(VIDEO_CODECS[key].label, key)
        self.acodec.clear()
        for key in c.audio:
            if key in self._audio_ok:
                self.acodec.addItem(AUDIO_CODECS[key].label, key)
        self._set(self.vcodec, cur_v)
        self._set(self.acodec, cur_a)
        self.agroup.setEnabled(bool(c.audio))
        self.agroup.setTitle("소리" if c.audio else "소리 (이 형식은 소리를 담을 수 없음)")

    def _sync_res(self) -> None:
        mode = self.res_mode.currentData()
        self.res_preset.setVisible(mode == "preset")
        self.res_w.setVisible(mode in ("custom", "fit_width"))
        self.res_h.setVisible(mode in ("custom", "fit_height"))
        self.res_x.setVisible(mode == "custom")
        if mode == "preset":
            self._preset_to_spins()

    def _preset_to_spins(self) -> None:
        w, h = self.res_preset.currentData()
        self.res_w.setValue(w)
        self.res_h.setValue(h)

    def _sync_rate(self) -> None:
        self.quality.setEnabled(self.vbr.isChecked())
        self.bitrate.setEnabled(self.cbr.isChecked())

    # ── 프리셋 ─────────────────────────────────────────────────────────────
    def _reload_presets(self) -> None:
        self.preset.clear()
        self.preset.addItem("프리셋 선택…", None)
        for name in BUILTIN_PRESETS:
            self.preset.addItem(name, ("builtin", name))
        for name in load_user_presets():
            self.preset.addItem(f"★ {name}", ("user", name))

    def _apply_preset(self) -> None:
        data = self.preset.currentData()
        if not data:
            return
        kind, name = data
        s = BUILTIN_PRESETS.get(name) if kind == "builtin" else load_user_presets().get(name)
        if s:
            s = s.fixed()
            if s.video_codec not in self._video_ok or (CONTAINERS[s.container].audio and s.audio_codec not in self._audio_ok):
                QMessageBox.information(self, "프리셋", "설치된 FFmpeg에 이 프리셋의 코덱이 없어 적용할 수 없습니다.")
                return
            self.load(s)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "프리셋 저장", "프리셋 이름")
        if ok and name.strip():
            save_user_preset(name.strip(), self.result_settings())
            self._reload_presets()

    def _delete_preset(self) -> None:
        data = self.preset.currentData()
        if not data or data[0] != "user":
            QMessageBox.information(self, "프리셋", "직접 저장한 프리셋(★)만 지울 수 있습니다.")
            return
        delete_user_preset(data[1])
        self._reload_presets()


