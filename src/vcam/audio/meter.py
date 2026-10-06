"""음량 측정. dBFS로 표현하며 무음은 SILENCE_DB로 고정한다."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

SILENCE_DB = -90.0


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
