"""동영상 편집 기능을 실제 FFmpeg로 검증한다."""

import subprocess
import threading

import pytest

from vcam.editing.formats import CONTAINERS, EncodeSettings, available_video_codecs
from vcam.editing.jobs import (
    Cancelled,
    EditError,
    EditJob,
    Piece,
    capture_frame,
    execute,
    load_media,
)
from vcam.editing.probe import keyframes, probe_file
from vcam.editing.segments import Segment, complement, split_equal
from vcam.editing.storage import ProjectFile

pytestmark = pytest.mark.integration


def make_video(ffmpeg, path, size="640x360", rate=30, seconds=10, audio=True):
    args = [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={seconds}"]  # fmt: skip
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    args += ["-c:v", "libx264", "-g", str(rate), "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    subprocess.run([*args, str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def media(ffmpeg, tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    a = make_video(ffmpeg, d / "a.mp4")
    b = make_video(ffmpeg, d / "b.mp4", size="320x240", rate=25, seconds=4)
    silent = make_video(ffmpeg, d / "silent.mp4", seconds=3, audio=False)
    mp3 = d / "song.mp3"
    subprocess.run([str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=6", "-c:a", "libmp3lame", str(mp3)], check=True)  # fmt: skip
    return {k: load_media(ffmpeg, p) for k, p in {"a": a, "b": b, "silent": silent, "mp3": mp3}.items()}


def job(out, pieces, **kw):
    return EditJob(pieces=tuple(pieces), output_dir=out, suffix=kw.pop("suffix", "_test"), **kw)


def dur(ffmpeg, path):
    return probe_file(ffmpeg, path).duration


def test_probe_and_keyframes(ffmpeg, media):
    a = media["a"]
    assert (a.width, a.height, a.vcodec, a.acodec) == (640, 360, "h264", "aac")
    assert abs(a.duration - 10) < 0.1 and abs(a.fps - 30) < 0.01
    keys = keyframes(ffmpeg, a.path)
    assert keys[:3] == pytest.approx([0.0, 1.0, 2.0], abs=0.05)


def test_fast_cut_snaps_to_keyframe(ffmpeg, media, tmp_path):
    r = execute(job(tmp_path, [Piece(media["a"], Segment(2.4, 5.0))], mode="fast"), ffmpeg)
    assert len(r.outputs) == 1 and r.outputs[0].suffix == ".mp4"
    assert abs(dur(ffmpeg, r.outputs[0]) - 3.0) < 0.15
    assert any("키프레임" in n for n in r.notes)


def test_encode_cut_is_frame_accurate(ffmpeg, media, tmp_path):
    r = execute(job(tmp_path, [Piece(media["a"], Segment(2.4, 5.0))], mode="encode"), ffmpeg)
    assert abs(dur(ffmpeg, r.outputs[0]) - 2.6) < 0.06
    info = probe_file(ffmpeg, r.outputs[0])
    assert info.has_audio and (info.width, info.height) == (640, 360)


@pytest.mark.parametrize("mode", ["fast", "encode"])
def test_multi_segments_merged(ffmpeg, media, tmp_path, mode):
    pieces = [Piece(media["a"], Segment(1, 3)), Piece(media["a"], Segment(6, 8))]
    r = execute(job(tmp_path, pieces, mode=mode, merge=True), ffmpeg)
    assert len(r.outputs) == 1
    assert abs(dur(ffmpeg, r.outputs[0]) - 4.0) < 0.2


def test_segments_saved_separately_with_timestamps(ffmpeg, media, tmp_path):
    pieces = [Piece(media["a"], Segment(1, 3)), Piece(media["a"], Segment(6, 8))]
    r = execute(job(tmp_path, pieces, mode="fast", merge=False, save_timestamps=True), ffmpeg)
    videos = [p for p in r.outputs if p.suffix == ".mp4"]
    stamps = [p for p in r.outputs if p.suffix == ".txt"]
    assert [p.name for p in videos] == ["a_test_01.mp4", "a_test_02.mp4"]
    assert len(stamps) == 2 and "a.mp4" in stamps[0].read_text(encoding="utf-8-sig")


def test_remove_segments(ffmpeg, media, tmp_path):
    keep = complement([Segment(2, 4), Segment(7, 8)], media["a"].duration)
    r = execute(job(tmp_path, [Piece(media["a"], s) for s in keep], mode="encode", suffix="_구간제거"), ffmpeg)
    assert abs(dur(ffmpeg, r.outputs[0]) - 7.0) < 0.1
    assert r.outputs[0].name == "a_구간제거.mp4"


def test_split_equal_parts(ffmpeg, media, tmp_path):
    parts = split_equal(3, media["a"].duration)
    r = execute(job(tmp_path, [Piece(media["a"], s) for s in parts], mode="encode", merge=False), ffmpeg)
    assert len(r.outputs) == 3
    assert abs(sum(dur(ffmpeg, p) for p in r.outputs) - 10) < 0.2


def test_merge_different_files(ffmpeg, media, tmp_path):
    pieces = [Piece(media["a"], Segment(0, media["a"].duration)), Piece(media["b"], Segment(0, media["b"].duration)),
              Piece(media["silent"], Segment(0, media["silent"].duration))]  # fmt: skip
    with pytest.raises(EditError, match="인코딩 모드"):
        execute(job(tmp_path, pieces, mode="fast"), ffmpeg)
    r = execute(job(tmp_path, pieces, mode="encode", suffix="_합치기"), ffmpeg)
    info = probe_file(ffmpeg, r.outputs[0])
    assert abs(info.duration - 17) < 0.3
    assert (info.width, info.height) == (640, 360) and info.has_audio


def test_merge_same_format_fast(ffmpeg, media, tmp_path):
    a = media["a"]
    pieces = [Piece(a, Segment(0, a.duration)), Piece(a, Segment(0, a.duration))]
    r = execute(job(tmp_path, pieces, mode="fast"), ffmpeg)
    assert abs(dur(ffmpeg, r.outputs[0]) - 20) < 0.3


@pytest.mark.parametrize("fmt,ext,codec", [("mp3", ".mp3", "mp3"), ("m4a", ".m4a", "aac"), ("wav", ".wav", "pcm_s16le"),
                                           ("flac", ".flac", "flac"), ("ogg", ".ogg", "vorbis")])  # fmt: skip
def test_extract_audio_only(ffmpeg, media, tmp_path, fmt, ext, codec):
    r = execute(job(tmp_path, [Piece(media["a"], Segment(0, 4))], save_video=False, extract_audio=fmt), ffmpeg)
    assert len(r.outputs) == 1 and r.outputs[0].suffix == ext
    info = probe_file(ffmpeg, r.outputs[0])
    assert not info.has_video and info.acodec == codec and abs(info.duration - 4) < 0.1


def test_remove_audio_and_separate(ffmpeg, media, tmp_path):
    r = execute(job(tmp_path, [Piece(media["a"], Segment(0, media["a"].duration))], mode="fast",
                    remove_audio=True, extract_audio="mp3"), ffmpeg)  # fmt: skip
    video, audio = r.outputs
    assert not probe_file(ffmpeg, video).has_audio
    assert probe_file(ffmpeg, audio).acodec == "mp3"


CONVERT = [("mp4", "h264", "h264"), ("mkv", "hevc", "hevc"), ("webm", "vp9", "vp9"), ("avi", "xvid", "mpeg4"),
           ("mov", "h264", "h264"), ("wmv", "wmv2", "wmv2"), ("flv", "flv1", "flv1"), ("m4v", "h264", "h264"),
           ("ts", "h264", "h264"), ("mpg", "mpeg2", "mpeg2video"), ("mkv", "av1", "av1"), ("webm", "vp8", "vp8"),
           ("avi", "mjpeg", "mjpeg"), ("gif", "gif", "gif")]  # fmt: skip


@pytest.mark.parametrize("container,vcodec,expect", CONVERT)
def test_convert_formats(ffmpeg, media, tmp_path, container, vcodec, expect):
    if vcodec not in available_video_codecs(ffmpeg):
        pytest.skip(f"{vcodec} 인코더 없음")
    s = EncodeSettings(container=container, video_codec=vcodec, resolution="fit_width", width=320, quality=60)
    r = execute(job(tmp_path, [Piece(media["a"], Segment(0, 2))], mode="encode", encode=s), ffmpeg)
    out = r.outputs[0]
    assert out.suffix == CONTAINERS[container].ext
    info = probe_file(ffmpeg, out)
    assert info.vcodec == expect and info.width == 320
    assert info.has_audio == (container != "gif")


def test_speed_rotate_resolution(ffmpeg, media, tmp_path):
    s = EncodeSettings(speed=2.0, rotate=90, fps=15, normalize=True)
    r = execute(job(tmp_path, [Piece(media["a"], Segment(0, 6))], mode="encode", encode=s), ffmpeg)
    info = probe_file(ffmpeg, r.outputs[0])
    assert abs(info.duration - 3.0) < 0.1
    assert (info.width, info.height) == (360, 640)
    assert abs(info.fps - 15) < 0.1


def test_preset_resolution_letterbox_and_cbr(ffmpeg, media, tmp_path):
    s = EncodeSettings(resolution="preset", width=320, height=320, rate_control="cbr", bitrate_kbps=500,
                       audio_channels=1, sample_rate=22050, audio_bitrate_kbps=96)  # fmt: skip
    r = execute(job(tmp_path, [Piece(media["a"], Segment(0, 2))], mode="encode", encode=s), ffmpeg)
    info = probe_file(ffmpeg, r.outputs[0])
    assert (info.width, info.height) == (320, 320)
    assert (info.channels, info.sample_rate) == (1, 22050)


def test_audio_file_editing(ffmpeg, media, tmp_path):
    r = execute(job(tmp_path, [Piece(media["mp3"], Segment(1, 4))], mode="encode"), ffmpeg)
    info = probe_file(ffmpeg, r.outputs[0])
    assert r.outputs[0].suffix == ".mp3" and not info.has_video and abs(info.duration - 3) < 0.1


def test_capture_frame(ffmpeg, media, tmp_path):
    png = capture_frame(ffmpeg, media["a"], 3.5, tmp_path)
    assert png.suffix == ".png" and png.stat().st_size > 1000


def test_cancel_leaves_no_files(ffmpeg, media, tmp_path):
    cancel = threading.Event()
    seen = []

    def progress(frac, label):
        seen.append(frac)
        if frac > 0.05:
            cancel.set()

    s = EncodeSettings(container="mkv", video_codec="av1" if "av1" in available_video_codecs(ffmpeg) else "h264", quality=100)
    out = tmp_path / "out"
    with pytest.raises(Cancelled):
        execute(job(out, [Piece(media["a"], Segment(0, 10))], mode="encode", encode=s), ffmpeg, cancel, progress)
    assert not list(out.iterdir()), "취소하면 결과·임시 파일이 남지 않아야 한다"


def test_project_roundtrip(tmp_path):
    p = ProjectFile(files=[tmp_path / "a.mp4"], segments={str(tmp_path / "a.mp4"): [Segment(1, 2)]},
                    tool="remove", mode="encode", encode=EncodeSettings(container="webm"))  # fmt: skip
    path = tmp_path / "x.vcamproj"
    p.save(path)
    q = ProjectFile.load(path)
    assert q.files == p.files and q.segments == p.segments and q.encode.container == "webm" and q.tool == "remove"
