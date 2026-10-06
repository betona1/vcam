import subprocess
from pathlib import Path

from vcam.encoding import encoder_probe
from vcam.encoding.encoder_probe import encoder_args, select_encoder
from vcam.encoding.ffmpeg import FfmpegPaths

FF = FfmpegPaths(Path("ffmpeg.exe"), Path("ffprobe.exe"))


def runner_ok(*codecs):
    calls = []

    def run(args, timeout):
        codec = args[args.index("-c:v") + 1]
        calls.append(codec)
        return subprocess.CompletedProcess(args, 0 if codec in codecs else 1, "", "")

    return run, calls


def setup_function():
    encoder_probe.clear_cache()


def test_order_nvenc_qsv_amf_x264():
    run, calls = runner_ok("libx264")
    sel = select_encoder(FF, "auto", run)
    assert sel.key == "x264"
    assert calls == ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]


def test_first_working_hardware_wins():
    run, calls = runner_ok("h264_qsv", "libx264")
    assert select_encoder(FF, "auto", run).key == "qsv"
    assert calls == ["h264_nvenc", "h264_qsv"]


def test_explicit_preference_falls_back_to_x264():
    run, calls = runner_ok("libx264")
    assert select_encoder(FF, "amf", run).key == "x264"
    assert calls == ["h264_amf", "libx264"]


def test_nothing_available():
    run, _ = runner_ok()
    assert select_encoder(FF, "auto", run).key == ""


def test_results_are_cached():
    run, calls = runner_ok("h264_nvenc")
    select_encoder(FF, "auto", run)
    select_encoder(FF, "auto", run)
    assert calls == ["h264_nvenc"]


def test_quality_args():
    args = encoder_args("x264", "high")
    assert args[args.index("-crf") + 1] == "19"
    assert "-cq" in encoder_args("nvenc", "small")
