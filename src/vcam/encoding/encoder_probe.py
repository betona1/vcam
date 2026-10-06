"""하드웨어 인코더 감지. 장치 이름으로 추측하지 않고 실제 짧은 인코딩으로 확인한다."""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass

from vcam.encoding.ffmpeg import FfmpegPaths, run

log = logging.getLogger(__name__)

ENCODER_ORDER: tuple[str, ...] = ("nvenc", "qsv", "amf", "x264")
HARDWARE_ENCODERS = frozenset({"nvenc", "qsv", "amf"})
FFMPEG_CODEC = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf", "x264": "libx264"}

# 품질 프리셋별 인코더 인자
_QUALITY = {
    "x264": {"small": 28, "balanced": 23, "high": 19},
    "nvenc": {"small": 30, "balanced": 25, "high": 20},
    "qsv": {"small": 30, "balanced": 25, "high": 20},
    "amf": {"small": 30, "balanced": 25, "high": 20},
}

Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def encoder_args(key: str, quality: str) -> list[str]:
    q = _QUALITY[key].get(quality, _QUALITY[key]["balanced"])
    codec = ["-c:v", FFMPEG_CODEC[key]]
    match key:
        case "x264":
            return [*codec, "-preset", "veryfast", "-crf", str(q), "-tune", "zerolatency"]
        case "nvenc":
            return [*codec, "-preset", "p4", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
        case "qsv":
            return [*codec, "-preset", "veryfast", "-global_quality", str(q)]
        case "amf":
            return [*codec, "-quality", "speed", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q)]
    raise ValueError(key)


def probe_encoder(ffmpeg: FfmpegPaths, key: str, runner: Runner = run) -> bool:
    args = [
        str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30", "-frames:v", "10",
        *encoder_args(key, "balanced"), "-pix_fmt", "yuv420p", "-f", "null", "-",
    ]  # fmt: skip
    try:
        result = runner(args, 20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("인코더 %s 시험 실패: %s", key, exc)
        return False
    ok = result.returncode == 0
    log.info("인코더 %s 시험 결과: %s %s", key, "성공" if ok else "실패", (result.stderr or "").strip()[:200])
    return ok


@dataclass(frozen=True)
class EncoderSelection:
    key: str
    tried: tuple[tuple[str, bool], ...]


_cache: dict[tuple[str, str], bool] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def select_encoder(ffmpeg: FfmpegPaths, preference: str = "auto", runner: Runner = run) -> EncoderSelection:
    """NVENC → QuickSync → AMF → x264 순으로 실제 시험해 처음 성공한 인코더를 고른다."""
    order = ENCODER_ORDER if preference == "auto" else (preference, "x264")
    tried: list[tuple[str, bool]] = []
    for key in dict.fromkeys(order):
        cache_key = (str(ffmpeg.ffmpeg), key)
        with _cache_lock:
            cached = _cache.get(cache_key)
        ok = cached if cached is not None else probe_encoder(ffmpeg, key, runner)
        with _cache_lock:
            _cache[cache_key] = ok
        tried.append((key, ok))
        if ok:
            return EncoderSelection(key, tuple(tried))
    return EncoderSelection("", tuple(tried))
