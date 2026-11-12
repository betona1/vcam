"""음량 측정. dBFS로 표현하며 무음은 SILENCE_DB로 고정한다.

SilenceWatch는 녹화 중 시스템 소리가 계속 디지털 무음인지 지켜본다. 루프백이 소리가 지나가지 않는
장치(예: 쓰지 않는 광출력)에 붙어 있으면 완전한 0만 들어오므로, 이를 사용자에게 알리는 근거가 된다.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

SILENCE_DB = -90.0
# 이보다 작은 피크는 디지털 무음으로 본다(실제 재생 중인 소리는 아무리 작아도 이 값 위에 있다).
DIGITAL_SILENCE_DB = -89.0
SILENCE_WARN_S = 5.0


def block_levels(block: np.ndarray) -> tuple[float, float]:
    """(peak_db, rms_db)"""
    if block.size == 0:
        return SILENCE_DB, SILENCE_DB
    peak = float(np.max(np.abs(block)))
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return _to_db(peak), _to_db(rms)


def _to_db(value: float) -> float:
    return max(SILENCE_DB, 20 * math.log10(value)) if value > 0 else SILENCE_DB


@dataclass(frozen=True)
class Level:
    peak_db: float = SILENCE_DB
    rms_db: float = SILENCE_DB


class LevelMeter:
    """오디오 스레드가 쓰고 GUI 스레드가 읽는다. 피크는 천천히 떨어진다."""

    DECAY_DB_PER_UPDATE = 1.5

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._level = Level()

    def update(self, block: np.ndarray) -> None:
        peak, rms = block_levels(block)
        with self._lock:
            held = max(peak, self._level.peak_db - self.DECAY_DB_PER_UPDATE)
            self._level = Level(held, rms)

    def reset(self) -> None:
        with self._lock:
            self._level = Level()

    def read(self) -> Level:
        with self._lock:
            return self._level


class SilenceWatch:
    """피크가 hold_s 동안 계속 디지털 무음이면 True. GUI 스레드에서 주기적으로 update()한다.
    일시정지·장치 재연결처럼 무음이 정상인 구간에서는 reset()해 세지 않는다."""

    def __init__(self, hold_s: float = SILENCE_WARN_S, threshold_db: float = DIGITAL_SILENCE_DB) -> None:
        self.hold_s = hold_s
        self.threshold_db = threshold_db
        self._since: float | None = None

    def update(self, peak_db: float, now: float) -> bool:
        if peak_db > self.threshold_db:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        return now - self._since >= self.hold_s

    @property
    def since(self) -> float | None:
        """조용해지기 시작한 시각(update에 넘긴 now 기준). 소리가 나는 중이면 None."""
        return self._since

    def reset(self) -> None:
        self._since = None
