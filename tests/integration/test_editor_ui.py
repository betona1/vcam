"""편집기 창을 실제로 조작해 각 작업이 끝까지 동작하는지 확인한다."""

import pytest

from vcam.editing.probe import probe_file
from vcam.encoding.ffmpeg import find_ffmpeg
from vcam.ui.editor.editor_window import EditorWindow
from vcam.ui.theme import apply_theme

from .test_editing import make_video

pytestmark = pytest.mark.integration


@pytest.fixture
def editor(qtbot, qapp, ffmpeg, tmp_path):
    palette = apply_theme(qapp, "dark")
    w = EditorWindow(palette, find_ffmpeg)
    qtbot.addWidget(w)
    a = make_video(ffmpeg, tmp_path / "a.mp4")
    b = make_video(ffmpeg, tmp_path / "b.mp4", size="320x240", rate=25, seconds=4)
    w.open_files([a, b])
    qtbot.waitUntil(lambda: len(w.files) == 2, timeout=15_000)
    w.file_list.setCurrentRow(0)
    return w


def run(qtbot, w):
    w.start()
    qtbot.waitUntil(lambda: w._cancel is None, timeout=120_000)
    assert "완료" in w.status.text(), w.status.text()
    return w._outputs


def mark(w, a, b):
    w.player.seek(a)
    w.player._flush_seek()
    w.mark_in()
    w.player.seek(b)
    w.player._flush_seek()
    w.mark_out()
    w.add_segment()


def test_cut_two_segments(qtbot, editor, ffmpeg):
    editor._select_tool("cut")
    editor.mode_encode.setChecked(True)
    mark(editor, 1.0, 3.0)
    mark(editor, 5.0, 6.0)
    assert editor.seg_tree.topLevelItemCount() == 2
    out = run(qtbot, editor)
    assert len(out) == 1 and abs(probe_file(ffmpeg, out[0]).duration - 3.0) < 0.15


def test_remove_segment(qtbot, editor, ffmpeg):
    editor._select_tool("remove")
    editor.mode_encode.setChecked(True)
    mark(editor, 2.0, 5.0)
    out = run(qtbot, editor)
    assert abs(probe_file(ffmpeg, out[0]).duration - 7.0) < 0.15


def test_split_equal(qtbot, editor):
    editor._select_tool("split")
    editor.split_equal.setChecked(True)
    editor.split_count.setValue(4)
    assert len(run(qtbot, editor)) == 4


def test_merge_needs_encoding_for_different_files(qtbot, editor, ffmpeg, monkeypatch):
    monkeypatch.setattr("vcam.ui.editor.editor_window.QMessageBox.warning", lambda *a, **k: None)
    editor._select_tool("merge")
    editor.mode_fast.setChecked(True)
    editor.start()
    qtbot.waitUntil(lambda: editor._cancel is None, timeout=60_000)
    assert "인코딩 모드" in editor.status.text()
    editor.mode_encode.setChecked(True)
    out = run(qtbot, editor)
    assert abs(probe_file(ffmpeg, out[0]).duration - 14.0) < 0.3


def test_extract_audio_all_files(qtbot, editor, ffmpeg):
    editor._select_tool("extract")
    editor.audio_fmt.setCurrentIndex(editor.audio_fmt.findData("mp3"))
    editor.opt_all.setChecked(True)
    out = run(qtbot, editor)
    assert sorted(p.suffix for p in out) == [".mp3", ".mp3"]


def test_mute_and_convert(qtbot, editor, ffmpeg):
    editor._select_tool("mute")
    out = run(qtbot, editor)
    assert not probe_file(ffmpeg, out[0]).has_audio
    editor._select_tool("convert")
    from vcam.editing.formats import EncodeSettings

    editor.encode = EncodeSettings(container="webm", video_codec="vp9", audio_codec="opus", resolution="fit_width", width=320)
    out = run(qtbot, editor)
    info = probe_file(ffmpeg, out[0])
    assert out[0].suffix == ".webm" and info.vcodec == "vp9" and info.acodec == "opus"


def test_helpful_message_without_segments(qtbot, editor, monkeypatch):
    shown = []
    monkeypatch.setattr("vcam.ui.editor.editor_window.QMessageBox.information", lambda *a, **k: shown.append(a[2]))
    editor._select_tool("cut")
    editor.start()
    assert shown and "구간" in shown[0]
