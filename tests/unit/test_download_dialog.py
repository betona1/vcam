"""내려받기 창: 옵션 ↔ 설정, 주소 추가, 안내 메시지, 결과 신호 (실제 내려받기 없음)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox

from vcam.download import engine
from vcam.download.engine import DownloadResult, MediaCandidate
from vcam.download.queue import DONE
from vcam.services.profile_service import Settings
from vcam.ui.download_dialog import DownloadDialog
from vcam.ui.theme import apply_theme


@pytest.fixture(autouse=True)
def fake_engine(monkeypatch, tmp_path):
    """yt-dlp 없이도 창이 동작하게: 설치됨으로 보고, 확인·내려받기는 가짜로."""
    monkeypatch.setattr(engine, "availability", lambda: engine.Availability(True))
    monkeypatch.setattr(engine, "engine_version", lambda: "test")

    def fake_probe(url, options, js_runtime=None):
        return MediaCandidate(url=url, title="제목 " + url[-1], duration_s=65, uploader="채널")

    def fake_download(url, options, ffmpeg, *, progress, log_fn, cancel, js_runtime):
        out = Path(options.output_dir) / f"제목 {url[-1]}.mp4"
        out.write_bytes(b"v")
        log_fn("[info] 받음")
        return DownloadResult(files=[out])

    monkeypatch.setattr(engine, "probe", fake_probe)
    monkeypatch.setattr(engine, "download", fake_download)


_FAKE_FFMPEG = object()


def make_dialog(qtbot, qapp, settings=None, ffmpeg=_FAKE_FFMPEG):
    d = DownloadDialog(settings or Settings(), apply_theme(qapp, "dark"), lambda: ffmpeg)
    qtbot.addWidget(d)
    return d


def test_options_follow_settings(qtbot, qapp, tmp_path):
    s = Settings(output_dir=str(tmp_path), download_kind="audio", download_audio_format="m4a", download_quality="720",
                 download_subtitles=True, download_auto_subtitles=True, download_subtitle_langs=("ja",),
                 download_playlist=True, download_make_subtitles=True)  # fmt: skip
    d = make_dialog(qtbot, qapp, s)
    o = d.options()
    assert o.kind == "audio" and o.audio_format == "m4a" and o.quality == "720" and o.playlist
    assert o.subtitles and o.auto_subtitles and o.subtitle_langs == ("ja.*",)
    assert o.output_dir == str(tmp_path)
    assert d.chk_make_subtitles.isChecked()
    assert d._settings_changes()["download_subtitle_langs"] == ("ja",)


def test_cookie_source_widgets_and_options(qtbot, qapp, monkeypatch, tmp_path):
    d = make_dialog(qtbot, qapp, Settings(output_dir=str(tmp_path), download_cookies_browser="firefox"))
    assert d.options().cookies_browser == "firefox" and d.options().cookies_file == ""
    assert d.lbl_cookie_hint.isVisibleTo(d) and not d.edt_cookie_file.isVisibleTo(d)
    d.cmb_cookies.setCurrentIndex(d.cmb_cookies.findData(""))
    assert d.options().cookies_browser == "" and not d.lbl_cookie_hint.isVisibleTo(d)
    # cookies.txt 모드: 파일이 없으면 추가하지 않고 알린다
    d.cmb_cookies.setCurrentIndex(d.cmb_cookies.findData("file"))
    assert d.edt_cookie_file.isVisibleTo(d) and d.btn_cookie_file.isVisibleTo(d)
    d.edt_cookie_file.setText(str(tmp_path / "없음.txt"))
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    assert d.add_urls(["https://x/a"]) == [] and shown and "cookies.txt" in shown[0]
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    d.edt_cookie_file.setText(str(cookie))
    saved = []
    d.settings_changed.connect(saved.append)
    items = d.add_urls(["https://x/a"])
    assert len(items) == 1 and items[0].options.cookies_browser == "file" and items[0].options.cookies_file == str(cookie)
    assert saved[0]["download_cookies_browser"] == "file" and saved[0]["download_cookies_file"] == str(cookie)
    qtbot.waitUntil(lambda: all(i.state == DONE for i in d.queue.items), timeout=5000)
    d.close()


def test_subtitles_need_a_language(qtbot, qapp):
    d = make_dialog(qtbot, qapp, Settings(download_subtitles=True, download_subtitle_langs=()))
    assert d.options().subtitles is False and d.options().subtitle_langs == ()


def test_add_without_url_informs(qtbot, qapp, monkeypatch):
    d = make_dialog(qtbot, qapp)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    d.edt_url.setText("주소 아님")
    d.add_from_input()
    assert shown and "주소" in shown[0]
    assert d.queue.items == []


def test_audio_without_ffmpeg_warns(qtbot, qapp, monkeypatch, tmp_path):
    d = make_dialog(qtbot, qapp, Settings(output_dir=str(tmp_path), download_kind="audio"), ffmpeg=None)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    assert d.add_urls(["https://x/a"]) == []
    assert shown and "FFmpeg" in shown[0]


def test_add_urls_runs_queue_and_emits_signals(qtbot, qapp, tmp_path):
    d = make_dialog(qtbot, qapp, Settings(output_dir=str(tmp_path), download_make_subtitles=True))
    saved, added, subs = [], [], []
    d.settings_changed.connect(saved.append)
    d.files_added.connect(added.append)
    d.subtitles_requested.connect(subs.append)
    d.edt_url.setText("https://x/a\nhttps://x/b")
    d.add_from_input()
    assert d.edt_url.text() == ""
    assert saved and saved[0]["download_kind"] == "video"
    items = d.queue.items
    assert len(items) == 2
    qtbot.waitUntil(lambda: all(i.state == DONE for i in d.queue.items), timeout=5000)
    qtbot.waitUntil(lambda: len(added) == 2 and len(subs) == 1, timeout=5000)
    assert [p.name for p in added[0]] == ["제목 a.mp4"]
    assert sorted(p.name for p in subs[0]) == ["제목 a.mp4", "제목 b.mp4"]
    assert d.tree.topLevelItemCount() == 2
    row = d.tree.topLevelItem(0)
    assert "제목 a" in row.text(0) and row.text(1) == "01:05" and row.text(2).startswith("완료")
    assert "받은 파일" in d.status.text()
    assert "받음" in d.log_view.toPlainText()
    d.close()


def test_clipboard_prefill_only_when_empty(qtbot, qapp):
    d = make_dialog(qtbot, qapp)
    QGuiApplication.clipboard().setText("https://youtu.be/clip")
    assert d.prefill_from_clipboard() is True
    assert d.edt_url.text() == "https://youtu.be/clip"
    QGuiApplication.clipboard().setText("https://youtu.be/other")
    assert d.prefill_from_clipboard() is False  # 이미 채워져 있으면 건드리지 않음
    QGuiApplication.clipboard().setText("")


def test_close_while_busy_cancels_after_confirm(qtbot, qapp, monkeypatch, tmp_path):
    import threading

    gate = threading.Event()

    def slow_download(url, options, ffmpeg, *, progress, log_fn, cancel, js_runtime):
        gate.wait(3)
        if cancel():
            raise engine.Cancelled()
        return DownloadResult(files=[Path(options.output_dir) / "x.mp4"])

    monkeypatch.setattr(engine, "download", slow_download)
    d = make_dialog(qtbot, qapp, Settings(output_dir=str(tmp_path)))
    d.add_urls(["https://x/a"])
    deadline = time.monotonic() + 3
    while d.queue.items[0].state != "downloading" and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert d.is_busy
    d.close()
    gate.set()
    qtbot.waitUntil(lambda: not d.is_busy, timeout=5000)
