"""출력 형식·코덱 목록과 인코딩 설정. 실제 FFmpeg 빌드에 있는 인코더만 사용한다."""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import asdict, dataclass, field, replace
from typing import Literal

from vcam.encoding.ffmpeg import FfmpegPaths, run

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoCodec:
    key: str
    label: str
    encoders: tuple[str, ...]  # 앞에서부터 시험해 처음 동작하는 것을 쓴다(하드웨어 우선)
    quality: Literal["crf264", "crfav1", "crfvp9", "vp8", "qscale"]
    pix_fmt: str = "yuv420p"


VIDEO_CODECS: dict[str, VideoCodec] = {c.key: c for c in (
    VideoCodec("h264", "H.264 (AVC)", ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"), "crf264"),
    VideoCodec("hevc", "H.265 (HEVC)", ("hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"), "crf264"),
    VideoCodec("av1", "AV1", ("av1_nvenc", "av1_qsv", "av1_amf", "libsvtav1", "libaom-av1"), "crfav1"),
    VideoCodec("vp9", "VP9", ("libvpx-vp9",), "crfvp9"),
    VideoCodec("vp8", "VP8", ("libvpx",), "vp8"),
    VideoCodec("xvid", "Xvid (MPEG-4)", ("libxvid", "mpeg4"), "qscale"),
    VideoCodec("mpeg4", "MPEG-4 Part 2", ("mpeg4",), "qscale"),
    VideoCodec("mpeg2", "MPEG-2", ("mpeg2video",), "qscale"),
    VideoCodec("mpeg1", "MPEG-1", ("mpeg1video",), "qscale"),
    VideoCodec("mjpeg", "Motion JPEG", ("mjpeg",), "qscale", "yuvj420p"),
    VideoCodec("wmv2", "WMV (Windows Media 8)", ("wmv2",), "qscale"),
    VideoCodec("flv1", "FLV (Sorenson H.263)", ("flv",), "qscale"),
    VideoCodec("gif", "GIF 애니메이션", ("gif",), "qscale", "pal8"),
)}  # fmt: skip


@dataclass(frozen=True)
class AudioCodec:
    key: str
    label: str
    encoder: str
    lossless: bool = False
    rates: tuple[int, ...] = ()  # 허용 샘플레이트(비어 있으면 제한 없음)


AUDIO_CODECS: dict[str, AudioCodec] = {c.key: c for c in (
    AudioCodec("aac", "AAC", "aac"),
    AudioCodec("mp3", "MP3", "libmp3lame", rates=(22050, 24000, 32000, 44100, 48000)),
    AudioCodec("opus", "Opus", "libopus", rates=(48000, 24000, 16000, 12000, 8000)),
    AudioCodec("vorbis", "Vorbis", "libvorbis"),
    AudioCodec("flac", "FLAC (무손실)", "flac", lossless=True),
    AudioCodec("pcm", "PCM (무압축 WAV)", "pcm_s16le", lossless=True),
    AudioCodec("mp2", "MPEG-1 Layer II", "mp2", rates=(32000, 44100, 48000)),
    AudioCodec("wma", "WMA", "wmav2"),
)}  # fmt: skip


@dataclass(frozen=True)
class Container:
    key: str
    label: str
    ext: str
    muxer: str
    video: tuple[str, ...]
    audio: tuple[str, ...]


CONTAINERS: dict[str, Container] = {c.key: c for c in (
    Container("mp4", "MP4", ".mp4", "mp4", ("h264", "hevc", "av1", "mpeg4", "xvid"), ("aac", "mp3", "opus", "flac")),
    Container("mkv", "MKV (Matroska)", ".mkv", "matroska",
              ("h264", "hevc", "av1", "vp9", "vp8", "xvid", "mpeg4", "mpeg2", "mpeg1", "mjpeg"),
              ("aac", "mp3", "opus", "vorbis", "flac", "pcm", "mp2")),
    Container("webm", "WebM", ".webm", "webm", ("vp9", "vp8", "av1"), ("opus", "vorbis")),
    Container("avi", "AVI", ".avi", "avi", ("xvid", "mpeg4", "h264", "mjpeg", "mpeg2", "mpeg1"), ("mp3", "pcm", "mp2", "aac")),
    Container("mov", "MOV (QuickTime)", ".mov", "mov", ("h264", "hevc", "mjpeg", "mpeg4"), ("aac", "pcm", "mp3")),
    Container("wmv", "WMV", ".wmv", "asf", ("wmv2",), ("wma",)),
    Container("flv", "FLV", ".flv", "flv", ("h264", "flv1"), ("aac", "mp3")),
    Container("m4v", "M4V", ".m4v", "mp4", ("h264", "hevc"), ("aac",)),
    Container("ts", "TS (MPEG-TS)", ".ts", "mpegts", ("h264", "hevc", "mpeg2"), ("aac", "mp2", "mp3")),
    Container("mpg", "MPEG (MPG)", ".mpg", "mpeg", ("mpeg2", "mpeg1"), ("mp2",)),
    Container("gif", "GIF 애니메이션", ".gif", "gif", ("gif",), ()),
)}  # fmt: skip

# 소리만 저장하는 형식
AUDIO_FORMATS: dict[str, tuple[str, str, str]] = {  # key: (라벨, 확장자, 오디오 코덱)
    "mp3": ("MP3", ".mp3", "mp3"),
    "m4a": ("M4A (AAC)", ".m4a", "aac"),
    "wav": ("WAV (무압축)", ".wav", "pcm"),
    "flac": ("FLAC (무손실)", ".flac", "flac"),
    "ogg": ("OGG (Vorbis)", ".ogg", "vorbis"),
    "opus": ("Opus", ".opus", "opus"),
    "wma": ("WMA", ".wma", "wma"),
}

RESOLUTION_PRESETS: list[tuple[str, int, int]] = [
    ("4K (3840×2160)", 3840, 2160), ("1440p (2560×1440)", 2560, 1440), ("1080p (1920×1080)", 1920, 1080),
    ("720p (1280×720)", 1280, 720), ("480p (854×480)", 854, 480), ("360p (640×360)", 640, 360),
    ("320×240", 320, 240),
]  # fmt: skip
FPS_CHOICES = (0, 60, 50, 30, 25, 24, 15, 10)  # 0 = 원본
SAMPLE_RATES = (0, 48000, 44100, 32000, 22050)  # 0 = 원본


@dataclass(frozen=True)
class EncodeSettings:
    container: str = "mp4"
    video_codec: str = "h264"
    resolution: Literal["original", "preset", "fit_width", "fit_height", "custom"] = "original"
    width: int = 1920
    height: int = 1080
    fps: int = 0
    rate_control: Literal["vbr", "cbr"] = "vbr"
    quality: int = 80  # VBR 0~100%
    bitrate_kbps: int = 8000  # CBR
    deinterlace: Literal["auto", "always", "off"] = "auto"
    audio_codec: str = "aac"
    audio_bitrate_kbps: int = 192
    audio_channels: int = 0  # 0 원본, 1 모노, 2 스테레오
    sample_rate: int = 0
    normalize: bool = False
    speed: float = 1.0  # 0.5 ~ 4.0
    rotate: int = 0  # 0, 90, 180, 270 (시계 방향)
    flip_h: bool = False
    flip_v: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> EncodeSettings:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def fixed(self) -> EncodeSettings:
        """형식에 맞지 않는 코덱 조합을 기본값으로 바로잡는다."""
        c = CONTAINERS.get(self.container, CONTAINERS["mp4"])
        s = replace(self, container=c.key)
        if s.video_codec not in c.video:
            s = replace(s, video_codec=c.video[0])
        if c.audio and s.audio_codec not in c.audio:
            s = replace(s, audio_codec=c.audio[0])
        return replace(s, speed=min(4.0, max(0.5, s.speed)), quality=min(100, max(0, s.quality)))


@dataclass
class EncoderAvailability:
    available: set[str] = field(default_factory=set)
    working: dict[str, bool] = field(default_factory=dict)


_avail_lock = threading.Lock()
_avail: dict[str, EncoderAvailability] = {}


def _availability(ffmpeg: FfmpegPaths) -> EncoderAvailability:
    with _avail_lock:
        cached = _avail.get(str(ffmpeg.ffmpeg))
        if cached is not None:
            return cached
        try:
            out = run([str(ffmpeg.ffmpeg), "-hide_banner", "-encoders"], timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        names = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1 and line.startswith(" ")}
        result = EncoderAvailability(available=names)
        _avail[str(ffmpeg.ffmpeg)] = result
        return result


def available_video_codecs(ffmpeg: FfmpegPaths) -> set[str]:
    names = _availability(ffmpeg).available
    return {k for k, c in VIDEO_CODECS.items() if any(e in names for e in c.encoders)}


def available_audio_codecs(ffmpeg: FfmpegPaths) -> set[str]:
    names = _availability(ffmpeg).available
    return {k for k, c in AUDIO_CODECS.items() if c.encoder in names}


def pick_video_encoder(ffmpeg: FfmpegPaths, codec_key: str) -> str:
    """하드웨어 인코더는 실제로 짧게 인코딩해 보고 되는 것을 쓴다(장치 이름으로 추측하지 않음)."""
    av = _availability(ffmpeg)
    codec = VIDEO_CODECS[codec_key]
    for enc in codec.encoders:
        if enc not in av.available:
            continue
        if not any(enc.endswith(hw) for hw in ("_nvenc", "_qsv", "_amf")):
            return enc
        with _avail_lock:
            ok = av.working.get(enc)
        if ok is None:
            try:
                r = run([str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                         "color=c=black:s=320x240:r=30", "-frames:v", "5", "-c:v", enc, "-f", "null", "-"], timeout=20)  # fmt: skip
                ok = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            with _avail_lock:
                av.working[enc] = ok
            log.info("인코더 %s 시험: %s", enc, "성공" if ok else "실패")
        if ok:
            return enc
    raise ValueError(f"{codec.label} 인코더를 사용할 수 없습니다")


def video_quality_args(encoder: str, s: EncodeSettings) -> list[str]:
    q = s.quality / 100
    if s.rate_control == "cbr":
        b = f"{s.bitrate_kbps}k"
        args = ["-b:v", b, "-maxrate", b, "-bufsize", f"{s.bitrate_kbps * 2}k"]
        if encoder in ("libvpx-vp9", "libvpx"):
            args = ["-b:v", b, "-minrate", b, "-maxrate", b]
        elif encoder in ("libsvtav1", "libaom-av1"):  # 이 인코더들은 maxrate/bufsize를 받지 않는다
            args = ["-b:v", b]
        elif encoder.endswith("_nvenc"):
            args = ["-rc", "cbr", *args]
        return args
    if encoder in ("libx264", "libx265"):
        return ["-crf", str(round(40 - q * 25)), "-preset", "veryfast" if encoder == "libx264" else "fast"]
    if encoder.endswith("_nvenc"):
        return ["-rc", "vbr", "-cq", str(round(40 - q * 25)), "-b:v", "0", "-preset", "p4"]
    if encoder.endswith("_qsv"):
        return ["-global_quality", str(round(40 - q * 25))]
    if encoder.endswith("_amf"):
        qp = str(round(40 - q * 25))
        return ["-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
    if encoder == "libsvtav1":
        return ["-crf", str(round(55 - q * 35)), "-preset", "8"]
    if encoder == "libaom-av1":
        return ["-crf", str(round(55 - q * 35)), "-b:v", "0", "-cpu-used", "6", "-row-mt", "1"]
    if encoder == "libvpx-vp9":
        return ["-crf", str(round(50 - q * 30)), "-b:v", "0", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1"]
    if encoder == "libvpx":
        return ["-crf", str(round(50 - q * 40)), "-b:v", "20M", "-deadline", "good", "-cpu-used", "4"]
    if encoder == "gif":
        return []
    return ["-q:v", str(max(2, round(31 - q * 29)))]


def audio_args(s: EncodeSettings, codec_key: str | None = None) -> list[str]:
    codec = AUDIO_CODECS[codec_key or s.audio_codec]
    args = ["-c:a", codec.encoder]
    if not codec.lossless:
        args += ["-b:a", f"{s.audio_bitrate_kbps}k"]
    if s.audio_channels:
        args += ["-ac", str(s.audio_channels)]
    rate = s.sample_rate
    if codec.rates and (rate or 48000) not in codec.rates:
        rate = codec.rates[0] if 48000 not in codec.rates else 48000
    elif codec.rates and not rate:
        rate = 48000 if 48000 in codec.rates else codec.rates[0]
    if rate:
        args += ["-ar", str(rate)]
    return args
