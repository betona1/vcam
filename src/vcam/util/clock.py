"""세션 공통 시간 기준. 일시정지 시간은 결과 시간축에서 빠진다."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class SessionClock:
    def __init__(self, now_ns: Callable[[], int] = time.perf_counter_ns) -> None:
        self._now = now_ns
        self._lock = threading.Lock()
        self._start_ns: int | None = None
        self._paused_at: int | None = None
        self._paused_total = 0
        self._pause_pending = False

    def start(self) -> int:
        with self._lock:
            self._start_ns = self._now()
            # 캡처가 열리기 전에 일시정지를 누른 경우: 시작하자마자 일시정지 상태로 둔다
            self._paused_at = self._start_ns if self._pause_pending else None
            self._paused_total = 0
            return self._start_ns

    @property
    def start_ns(self) -> int | None:
        return self._start_ns

    @property
    def is_paused(self) -> bool:
        return self._paused_at is not None or (self._start_ns is None and self._pause_pending)

    def pause(self) -> None:
        with self._lock:
            if self._start_ns is None:
                self._pause_pending = True
            elif self._paused_at is None:
                self._paused_at = self._now()

    def resume(self) -> None:
        with self._lock:
            self._pause_pending = False
            if self._paused_at is not None:
                self._paused_total += self._now() - self._paused_at
                self._paused_at = None

    def active_ns(self) -> int:
        """시작 이후 일시정지 시간을 뺀 경과 시간(ns)."""
        with self._lock:
            if self._start_ns is None:
                return 0
            end = self._paused_at if self._paused_at is not None else self._now()
            return end - self._start_ns - self._paused_total


def due_frame_index(active_ns: int, fps: int) -> int:
    """경과 시간에서 지금 기록되어야 할 프레임 슬롯 번호."""
    return active_ns * fps // 1_000_000_000


def slot_start_ns(index: int, fps: int) -> int:
    return -(-index * 1_000_000_000 // fps)
