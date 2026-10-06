"""코드 검토에서 찾은 버그의 재발 방지 테스트(실제 FFmpeg 사용)."""

import subprocess
import time

import pytest

from vcam.editing.formats import EncodeSettings, available_video_codecs
from vcam.editing.jobs import EditError, EditJob, Piece, execute, load_media
from vcam.editing.probe import keyframes, probe_file
from vcam.editing.segments import Segment, split_at
from vcam.util.clock import SessionClock

from .test_editing import make_video

pytestmark = pytest.mark.integration


def ff_run(ffmpeg, *args):
    subprocess.run([str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def frames(ffmpeg, path):
    out = subprocess.run([str(ffmpeg.ffprobe), "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    return int(out.strip())


def job(out, pieces, **kw):
    return EditJob(tuple(pieces), out, kw.pop("suffix", "_r"), **kw)


@pytest.fixture(scope="module")
def src(ffmpeg, tmp_path_factory):
    d = tmp_path_factory.mktemp("reg")
    mono = d / "mono.mp4"
    ff_run(ffmpeg, "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=10", "-f", "lavfi", "-i",
           "sine=duration=10:sample_rate=44100", "-ac", "1", "-c:v", "libx264", "-g", "90", "-bf", "0",
           "-tune", "zerolatency", "-c:a", "aac", "-shortest", str(mono))  # fmt: skip
    ts = d / "offset.ts"
    ff_run(ffmpeg, "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=10", "-c:v", "libx264", "-g", "30",
           "-output_ts_offset", "2.78", str(ts))  # fmt: skip
    wav = d / "voice.wav"
    ff_run(ffmpeg, "-f", "lavfi", "-i", "sine=duration=4", str(wav))
    return {"mono": load_media(ffmpeg, mono), "ts": load_media(ffmpeg, ts), "wav": load_media(ffmpeg, wav), "dir": d}


def test_keyframes_relative_to_file_start(ffmpeg, src, tmp_path):
    m = src["ts"]
    assert m.start_time > 2
    assert keyframes(ffmpeg, m.path, start_time=m.start_time)[:2] == pytest.approx([0.0, 1.0], abs=0.05)
    r = execute(job(tmp_path, [Piece(m, Segment(2.5, 5.0))]), ffmpeg)
    assert 2.5 <= probe_file(ffmpeg, r.outputs[0]).duration <= 3.2, "2초 키프레임에서 시작해야 한다(0초가 아님)"


def test_fast_split_is_frame_exact_without_bframes(ffmpeg, src, tmp_path):
    m = src["mono"]
    parts = split_at([3.0, 6.0], m.duration)
    r = execute(job(tmp_path, [Piece(m, s) for s in parts], merge=False), ffmpeg)
    assert [frames(ffmpeg, p) for p in r.outputs] == [90, 90, 120]


@pytest.mark.parametrize("container,acodec", [("mkv", "flac"), ("webm", "vorbis"), ("mkv", "pcm"), ("mp4", "aac")])
def test_loudnorm_keeps_normal_sample_rate(ffmpeg, src, tmp_path, container, acodec):
    vc = "vp9" if container == "webm" else "h264"
    s = EncodeSettings(container=container, video_codec=vc, audio_codec=acodec, normalize=True, resolution="fit_width", width=160)
    r = execute(job(tmp_path, [Piece(src["mono"], Segment(0, 2))], mode="encode", encode=s), ffmpeg)
    assert probe_file(ffmpeg, r.outputs[0]).sample_rate == 48000


def test_av1_cbr(ffmpeg, src, tmp_path):
    if "av1" not in available_video_codecs(ffmpeg):
        pytest.skip("AV1 인코더 없음")
    s = EncodeSettings(container="mkv", video_codec="av1", rate_control="cbr", bitrate_kbps=800, resolution="fit_width", width=160)
    r = execute(job(tmp_path, [Piece(src["mono"], Segment(0, 1))], mode="encode", encode=s), ffmpeg)
    assert probe_file(ffmpeg, r.outputs[0]).vcodec == "av1"


def test_concat_list_with_apostrophe_in_appdata(ffmpeg, src, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "O'Brien"))
    m = src["mono"]
    # 키프레임(3초 간격)에 맞춘 구간이라 빠른 모드에서도 길이가 정확하다
    r = execute(job(tmp_path / "out", [Piece(m, Segment(0, 3)), Piece(m, Segment(6, 9))]), ffmpeg)
    assert abs(probe_file(ffmpeg, r.outputs[0]).duration - 6) < 0.2


def test_cleanup_only_touches_own_temp_files(ffmpeg, src, tmp_path):
    other = tmp_path / ".mono_r_2.abc.vcam-tmp.mp4"
    other.write_bytes(b"x")
    execute(job(tmp_path, [Piece(src["mono"], Segment(0, 1))]), ffmpeg)
    assert other.exists(), "다른 작업의 임시 파일을 지우면 안 된다"


def test_mixed_video_and_audio_merge_is_refused(ffmpeg, src, tmp_path):
    with pytest.raises(EditError, match="소리 파일"):
        execute(job(tmp_path, [Piece(src["mono"], Segment(0, 2)), Piece(src["wav"], Segment(0, 2))], mode="encode"), ffmpeg)


def test_mute_on_audio_only_has_clear_message(ffmpeg, src, tmp_path):
    with pytest.raises(EditError, match="소리를 제거할 수 없습니다"):
        execute(job(tmp_path, [Piece(src["wav"], Segment(0, 2))], remove_audio=True), ffmpeg)


def test_audio_only_keeps_format_and_no_duplicate(ffmpeg, src, tmp_path):
    r = execute(job(tmp_path, [Piece(src["wav"], Segment(0, 2))], mode="encode"), ffmpeg)
    assert [p.suffix for p in r.outputs] == [".wav"], "WAV 편집 결과가 손실 압축 MP3로 바뀌면 안 된다"
    r = execute(job(tmp_path, [Piece(src["wav"], Segment(0, 2))], mode="encode", extract_audio="wav", suffix="_x"), ffmpeg)
    assert len(r.outputs) == 1, "같은 소리 파일을 두 번 만들면 안 된다"


def test_original_mono_is_kept(ffmpeg, src, tmp_path):
    r = execute(job(tmp_path, [Piece(src["mono"], Segment(0, 2))], save_video=False, extract_audio="wav"), ffmpeg)
    info = probe_file(ffmpeg, r.outputs[0])
    assert (info.channels, info.sample_rate) == (1, 44100)


def test_fps_applies_after_speed_and_timestamps_use_clamped_speed(ffmpeg, src, tmp_path):
    s = EncodeSettings(fps=60, speed=0.5, resolution="fit_width", width=160)
    r = execute(job(tmp_path, [Piece(src["mono"], Segment(0, 2))], mode="encode", encode=s), ffmpeg)
    assert abs(probe_file(ffmpeg, r.outputs[0]).fps - 60) < 0.5
    s = EncodeSettings(speed=0, resolution="fit_width", width=160)
    r = execute(job(tmp_path, [Piece(src["mono"], Segment(0, 1)), Piece(src["mono"], Segment(2, 3))], mode="encode",
                    encode=s, save_timestamps=True, suffix="_ts"), ffmpeg)  # fmt: skip
    assert any(p.suffix == ".txt" for p in r.outputs)


def test_xvid_fallback_tag_not_in_mp4(ffmpeg, src, tmp_path, monkeypatch):
    monkeypatch.setattr("vcam.editing.jobs.pick_video_encoder", lambda ff, key: "mpeg4")
    s = EncodeSettings(container="mp4", video_codec="xvid", resolution="fit_width", width=160)
    r = execute(job(tmp_path, [Piece(src["mono"], Segment(0, 1))], mode="encode", encode=s), ffmpeg)
    assert probe_file(ffmpeg, r.outputs[0]).vcodec == "mpeg4"


def test_clock_pause_before_start_is_kept():
    clock = SessionClock()
    clock.pause()
    clock.start()
    time.sleep(0.05)
    assert clock.is_paused and clock.active_ns() == 0
    clock.resume()
    time.sleep(0.05)
    assert clock.active_ns() > 0


def test_project_file_garbage(tmp_path):
    from vcam.editing.storage import ProjectFile

    bad = tmp_path / "bad.vcamproj"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        ProjectFile.load(bad)
    bad.write_text('{"encode": null, "files": ["a.mp4"]}', encoding="utf-8")
    assert ProjectFile.load(bad).files[0].name == "a.mp4"


# ── 편집기 화면 ─────────────────────────────────────────────────────────────
@pytest.fixture
def editor(qtbot, qapp, ffmpeg, tmp_path):
    from vcam.encoding.ffmpeg import find_ffmpeg
    from vcam.ui.editor.editor_window import EditorWindow
    from vcam.ui.theme import apply_theme

    w = EditorWindow(apply_theme(qapp, "dark"), find_ffmpeg)
    qtbot.addWidget(w)
    paths = [make_video(ffmpeg, tmp_path / f"{n}.mp4", seconds=4) for n in ("b", "e", "a")]
    w.open_files(paths)
    qtbot.waitUntil(lambda: len(w.files) == 3, timeout=20_000)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_remove_first_file_keeps_list_and_current_in_sync(editor):
    editor.file_list.setCurrentRow(0)
    editor.remove_current()
    assert editor.current.path.name == editor.file_list.currentItem().text()


def test_move_file_keeps_current_and_marks(editor):
    editor.file_list.setCurrentRow(1)
    editor.sel_in, editor.sel_out = 1.0, 2.0
    editor._move_file(-1)
    assert editor.current.path.name == "e.mp4" and (editor.sel_in, editor.sel_out) == (1.0, 2.0)
    assert [f.path.name for f in editor.files] == ["e.mp4", "b.mp4", "a.mp4"]


def test_tool_options_do_not_leak(editor):
    editor._select_tool("mute")
    editor._select_tool("extract")
    editor._select_tool("cut")
    editor.segments[editor.current.path] = [Segment(0, 1)]
    j = editor.build_jobs()[0]
    assert not j.remove_audio and j.extract_audio is None


def test_cut_and_remove_segments_are_separate(editor):
    editor._select_tool("cut")
    editor.segments[editor.current.path] = [Segment(0, 1)]
    editor._select_tool("remove")
    assert editor.current.path not in editor.segments


def test_keys_go_to_focused_inputs(qtbot, editor):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    editor._select_tool("cut")
    editor.sel_in, editor.sel_out = 0.5, 1.5
    editor.out_name.setFocus()
    QTest.keyClick(editor.out_name, Qt.Key.Key_Return)
    assert not editor.segments.get(editor.current.path), "파일 이름 칸에서 Enter는 구간을 추가하면 안 된다"
    editor.opt_stamp.setFocus()
    before = editor.opt_stamp.isChecked()
    QTest.keyClick(editor.opt_stamp, Qt.Key.Key_Space)
    assert editor.opt_stamp.isChecked() != before, "체크박스에서 Space는 체크를 바꿔야 한다"
    editor.timeline.setFocus()
    QTest.keyClick(editor.timeline, Qt.Key.Key_Return)
    assert editor.segments.get(editor.current.path), "타임라인에서는 Enter가 구간을 추가한다"


def test_delete_in_split_mode_does_nothing(editor):
    editor._select_tool("cut")
    editor.segments[editor.current.path] = [Segment(0, 1), Segment(2, 3)]
    editor._refresh_view()
    editor.seg_tree.setCurrentItem(editor.seg_tree.topLevelItem(1))
    editor._select_tool("split")
    editor.delete_segment()
    editor._select_tool("cut")
    assert len(editor.segments[editor.current.path]) == 2


def test_timeline_zoomed_hatch_is_fast(qapp):
    from vcam.ui.editor.timeline import Timeline
    from vcam.ui.theme import apply_theme

    t = Timeline(apply_theme(qapp, "dark"))
    t.resize(1200, 80)
    t.set_duration(7200)
    t.set_segments([Segment(0, 7000)], "remove")
    t.view = (100.0, 100.5)
    start = time.perf_counter()
    t.grab()
    assert time.perf_counter() - start < 0.3
