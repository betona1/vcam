"""자막 만들기 대화상자: 옵션 수집, 설정 저장 신호, 모델 없음 안내 (실제 모델 불필요)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from vcam.services.profile_service import Settings
from vcam.subtitles import transcriber as tr
from vcam.subtitles.pipeline import EngineCache
from vcam.ui.subtitle_dialog import SubtitleDialog, settings_to_options
from vcam.ui.theme import apply_theme


def make_dialog(qtbot, qapp, settings=None, ffmpeg=None):
    d = SubtitleDialog([Path("C:/v/a.mp4"), Path("C:/v/b.mp4")], settings or Settings(), apply_theme(qapp, "dark"),
                       lambda: ffmpeg, EngineCache())  # fmt: skip
    qtbot.addWidget(d)
    return d


def test_options_follow_settings(qtbot, qapp):
    s = Settings(subtitle_language="ja", subtitle_device="cpu", subtitle_translate=True, subtitle_targets=("ko", "en"),
                 subtitle_online=True, subtitle_max_chars=60, subtitle_model="small")  # fmt: skip
    d = make_dialog(qtbot, qapp, s)
    o = d.options()
    assert o.source_language == "ja" and o.device == "cpu" and o.model == "small"
    assert o.translate and o.targets == ("ko", "en") and o.allow_online and o.max_chars == 60
    assert settings_to_options(s).targets == ("ko", "en")


def test_translate_group_unchecked_means_no_translation(qtbot, qapp):
    d = make_dialog(qtbot, qapp, Settings(subtitle_translate=False, subtitle_targets=()))
    assert d.options().translate is False
    assert d.options().targets == ("ko",)  # 비어 있으면 기본값으로 저장


def test_start_without_ffmpeg_warns_and_does_not_run(qtbot, qapp, monkeypatch):
    d = make_dialog(qtbot, qapp, ffmpeg=None)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    d.start()
    assert shown and "FFmpeg" in shown[0]
    assert not d.is_busy


def test_start_without_model_informs_and_saves_nothing(qtbot, qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "is_model_cached", lambda name: False)
    monkeypatch.setattr(tr, "default_model", lambda: "small")
    d = make_dialog(qtbot, qapp, Settings(subtitle_download=False), ffmpeg=object())
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    saved: list[dict] = []
    d.settings_changed.connect(saved.append)
    d.start()
    assert shown and "PC에 없습니다" in shown[0]
    assert saved == [] and not d.is_busy


def test_model_notice_mentions_download_size(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(tr, "is_model_cached", lambda name: False)
    monkeypatch.setattr(tr, "default_model", lambda: "small")
    d = make_dialog(qtbot, qapp)
    assert "PC에 없습니다" in d.model_notice.text()
    d.chk_download.setChecked(True)
    assert "내려받습니다" in d.model_notice.text()
