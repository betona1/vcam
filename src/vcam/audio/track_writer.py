"""세션 시계에 맞춰 원시 PCM(s16le)을 기록한다.

블록이 도착한 시점의 세션 경과 시간과 지금까지 쓴 샘플 수를 비교해
- 늦으면(장치가 멈췄거나 시계가 느림) 무음을 채우고
- 앞서면(장치 시계가 빠름) 블록 앞부분을 잘라
오디오가 영상과 같은 시간축을 유지하게 한다. 일시정지 중 블록은 버린다.
원시 PCM은 헤더가 없어 비정상 종료 뒤에도 그대로 복구할 수 있다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vcam.util.clock import SessionClock

log = logging.getLogger(__name__)

TOLERANCE_S = 0.04  # 이 정도 오차는 블록 도착 지터로 보고 고치지 않는다


@dataclass(frozen=True)
class AudioTrack:
    """최종 믹스에 들어갈 트랙 정보(세션 메타데이터에도 저장)."""

    label: str
    path: Path
    samplerate: int
    channels: int

    def to_meta(self) -> dict:
        return {"label": self.label, "file": self.path.name, "rate": self.samplerate, "channels": self.channels}

    @classmethod
    def from_meta(cls, directory: Path, meta: dict) -> AudioTrack:
        return cls(meta["label"], directory / meta["file"], int(meta["rate"]), int(meta["channels"]))


@dataclass(frozen=True)
class TrackStats:
    label: str
    samples_written: int
    padded_samples: int
    trimmed_samples: int
    max_offset_ms: float
    end_offset_ms: float  # 마무리 직전 오디오 길이 - 영상 길이


def to_s16le(block: np.ndarray) -> bytes:
    return (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class AlignedPcmWriter:
    def __init__(self, track: AudioTrack, clock: SessionClock) -> None:
        self.track = track
        self.clock = clock
        self._file = open(track.path, "wb")  # noqa: SIM115 - 녹화 동안 열어 둔다
        self._lock = threading.Lock()
        self._written = 0  # 채널당 샘플 수
        self._padded = 0
        self._trimmed = 0
        self._max_offset = 0.0
        self._closed = False

    @property
    def samples_written(self) -> int:
        return self._written

    def _silence(self, frames: int) -> None:
        zeros = np.zeros((frames, self.track.channels), dtype=np.float32)
        self._file.write(to_s16le(zeros))
        self._written += frames
        self._padded += frames

    def write(self, block: np.ndarray, active_ns: int | None = None) -> None:
        """block은 방금 도착한 (프레임, 채널) float32. active_ns는 도착 시점의 세션 경과 시간."""
        with self._lock:
            if self._closed or self.clock.start_ns is None or self.clock.is_paused:
                return
            sr = self.track.samplerate
            now = self.clock.active_ns() if active_ns is None else active_ns
            expected_end = now * sr // 1_000_000_000
            frames = block.shape[0]
            offset = self._written + frames - expected_end  # 양수 = 오디오가 앞섬
            self._max_offset = max(self._max_offset, abs(offset) / sr * 1000)
            tol = int(TOLERANCE_S * sr)
            if offset < -tol:
                self._silence(int(-offset))
            elif offset > tol:
                cut = min(frames, int(offset))
                block = block[cut:]
                self._trimmed += cut
            if block.shape[0]:
                self._file.write(to_s16le(block))
                self._written += block.shape[0]

    def finish(self, duration_s: float) -> TrackStats:
        """영상 길이에 정확히 맞춰 무음을 덧붙이거나 잘라 낸 뒤 닫는다."""
        with self._lock:
            sr, ch = self.track.samplerate, self.track.channels
            target = round(duration_s * sr)
            end_offset_ms = (self._written - target) / sr * 1000
            if self._written < target:
                self._silence(target - self._written)
            elif self._written > target:
                self._file.truncate(target * ch * 2)
                self._trimmed += self._written - target
                self._written = target
            self._file.close()
            self._closed = True
            stats = TrackStats(self.track.label, self._written, self._padded, self._trimmed,
                               self._max_offset, end_offset_ms)  # fmt: skip
        log.info(
            "오디오 트랙 %s: %.2f초, 무음 보충 %.0fms, 잘라냄 %.0fms, 최대 오차 %.0fms, 끝 오차 %.0fms",
            stats.label, stats.samples_written / sr, stats.padded_samples / sr * 1000,
            stats.trimmed_samples / sr * 1000, stats.max_offset_ms, stats.end_offset_ms,
        )  # fmt: skip
        return stats

    def abort(self) -> None:
        with self._lock:
            if not self._closed:
                self._file.close()
                self._closed = True
