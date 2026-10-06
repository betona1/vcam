"""오디오 소스 프로토콜, 장치 정보, 테스트용 가짜 소스.

소스는 반드시 같은 스레드에서 open → read → close 순으로 사용한다.
read()는 (프레임 수, 채널) float32 배열을 돌려주며, 실제 시간만큼 블로킹한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

AudioKind = Literal["system", "microphone"]
SAMPLE_RATE = 48_000
BLOCK_FRAMES = 960  # 20ms


class AudioError(Exception):
    pass


@dataclass(frozen=True)
class AudioDevice:
    id: str  # 빈 문자열 = 시스템 기본 장치
    name: str
    kind: AudioKind
    is_default: bool = False


class AudioSource(Protocol):
    samplerate: int
    channels: int
    device_name: str

    def open(self) -> None: ...

    def read(self) -> np.ndarray: ...

    def close(self) -> None: ...


class FakeAudioSource:
    """사인파를 실시간 속도로 내보낸다. rate_skew로 장치 시계 오차를, fail_after로 장치 분리를 흉내 낸다."""

    def __init__(
        self,
        channels: int = 2,
        frequency: float = 440.0,
        amplitude: float = 0.3,
        rate_skew: float = 0.0,
        fail_after_blocks: int | None = None,
        samplerate: int = SAMPLE_RATE,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.device_name = "가짜 장치"
        self._freq = frequency
        self._amp = amplitude
        self._skew = rate_skew
        self._fail_after = fail_after_blocks
        self._blocks = 0
        self._phase = 0
        self._next_ns = 0

    def open(self) -> None:
        self._next_ns = time.perf_counter_ns()

    def read(self) -> np.ndarray:
        self._blocks += 1
        if self._fail_after is not None and self._blocks > self._fail_after:
            raise AudioError("가짜 오디오 장치가 분리되었습니다")
        # 장치 시계가 빠르면(rate_skew>0) 같은 블록이 실제보다 짧은 시간에 도착한다.
        self._next_ns += int(BLOCK_FRAMES / self.samplerate * 1e9 / (1.0 + self._skew))
        delay = (self._next_ns - time.perf_counter_ns()) / 1e9
        if delay > 0:
            time.sleep(delay)
        t = (np.arange(BLOCK_FRAMES) + self._phase) / self.samplerate
        self._phase += BLOCK_FRAMES
        wave = (self._amp * np.sin(2 * np.pi * self._freq * t)).astype(np.float32)
        return np.repeat(wave[:, None], self.channels, axis=1)

    def close(self) -> None:
        pass
