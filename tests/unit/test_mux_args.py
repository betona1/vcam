from pathlib import Path

from vcam.audio.track_writer import AudioTrack
from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.encoding.muxer import build_mux_args

FF = FfmpegPaths(Path("ffmpeg.exe"), Path("ffprobe.exe"))


def track(tmp_path, name, channels, size=100):
    path = tmp_path / f"{name}.pcm"
    path.write_bytes(b"\0" * size)
    return AudioTrack(name, path, 48000, channels)


def test_video_only_when_no_audio(tmp_path):
    args = build_mux_args(FF, tmp_path / "v.mkv", tmp_path / "o.mp4")
    assert "-filter_complex" not in args
    assert args[args.index("-c") + 1] == "copy"


def test_single_track_padded_to_video(tmp_path):
    args = build_mux_args(FF, tmp_path / "v.mkv", tmp_path / "o.mp4", [track(tmp_path, "system", 2)])
    graph = args[args.index("-filter_complex") + 1]
    assert "amix" not in graph and "apad" in graph
    assert "-shortest" in args and "aac" in args


def test_two_tracks_are_mixed_without_normalizing(tmp_path):
    args = build_mux_args(
        FF, tmp_path / "v.mkv", tmp_path / "o.mp4", [track(tmp_path, "system", 2), track(tmp_path, "microphone", 1)]
    )
    graph = args[args.index("-filter_complex") + 1]
    assert "amix=inputs=2" in graph and "normalize=0" in graph and "alimiter" in graph
    assert args.count("s16le") == 2
    channels = [args[i + 1] for i, a in enumerate(args) if a == "-ac"]
    assert channels == ["2", "1"]


def test_empty_or_missing_tracks_are_skipped(tmp_path):
    empty = track(tmp_path, "system", 2, size=0)
    missing = AudioTrack("microphone", tmp_path / "nope.pcm", 48000, 1)
    args = build_mux_args(FF, tmp_path / "v.mkv", tmp_path / "o.mp4", [empty, missing])
    assert "-filter_complex" not in args


def test_track_meta_roundtrip(tmp_path):
    t = AudioTrack("microphone", tmp_path / "microphone.pcm", 48000, 1)
    assert AudioTrack.from_meta(tmp_path, t.to_meta()) == t
