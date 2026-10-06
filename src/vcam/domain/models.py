"""녹화에 쓰이는 불변 데이터 모델. 모든 좌표는 물리 픽셀 기준이다."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SourceKind = Literal["display", "window", "region", "audio"]
QualityPreset = Literal["small", "balanced", "high", "custom"]
EncoderPreference = Literal["auto", "nvenc", "qsv", "amf", "x264"]

QUALITY_LABELS: dict[str, str] = {"small": "작은 용량", "balanced": "균형", "high": "고화질"}
ENCODER_LABELS: dict[str, str] = {
    "auto": "자동",
    "nvenc": "NVIDIA NVENC",
    "qsv": "Intel QuickSync",
    "amf": "AMD AMF",
    "x264": "소프트웨어(x264)",
}


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @classmethod
    def from_points(cls, x1: int, y1: int, x2: int, y2: int) -> Rect:
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        return cls(left, top, right - left, bottom - top)

    def contains_rect(self, other: Rect) -> bool:
        return (
            other.left >= self.left
            and other.top >= self.top
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def intersected(self, other: Rect) -> Rect:
        left, top = max(self.left, other.left), max(self.top, other.top)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return Rect(left, top, 0, 0)
        return Rect(left, top, right - left, bottom - top)

    def as_tuple(self) -> tuple[int, int, int, int]:
        """(left, top, width, height) — 설정 저장용."""
        return self.left, self.top, self.width, self.height

    def as_ltrb(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def __str__(self) -> str:
        return f"{self.width} × {self.height} @ ({self.left}, {self.top})"


@dataclass(frozen=True)
class MonitorInfo:
    index: int  # 1부터 시작하는 표시 번호
    rect: Rect
    is_primary: bool
    device_name: str = ""

    @property
    def label(self) -> str:
        primary = " · 주 모니터" if self.is_primary else ""
        return f"모니터 {self.index}  ({self.rect.width} × {self.rect.height}){primary}"


@dataclass(frozen=True)
class CaptureSource:
    kind: SourceKind
    rect: Rect
    label: str
    monitor_index: int | None = None


@dataclass(frozen=True)
class RecordingProfile:
    id: str = "default"
    name: str = "기본"
    source_kind: SourceKind = "display"
    fps: int = 30
    quality_preset: QualityPreset = "balanced"
    encoder_preference: EncoderPreference = "auto"
    system_audio_enabled: bool = True
    microphone_enabled: bool = False
    webcam_enabled: bool = False
    cursor_enabled: bool = True


@dataclass(frozen=True)
class SessionPaths:
    directory: Path
    video_partial: Path
    meta: Path
    ffmpeg_log: Path

    @classmethod
    def in_dir(cls, directory: Path) -> SessionPaths:
        return cls(
            directory=directory,
            video_partial=directory / "video.partial.mkv",
            meta=directory / "session.json",
            ffmpeg_log=directory / "ffmpeg.log",
        )


@dataclass(frozen=True)
class RecordingMetrics:
    elapsed_ns: int = 0
    frames_written: int = 0
    frames_dropped: int = 0
    measured_fps: float = 0.0
    file_bytes: int = 0
    encoder: str = ""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    codec: str
    size_bytes: int
    audio_codec: str = ""
    video_duration_s: float = 0.0
    audio_duration_s: float = 0.0


@dataclass(frozen=True)
class RecordingResult:
    path: Path
    media: MediaInfo
    encoder: str
    frames_dropped: int
    recovered: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
