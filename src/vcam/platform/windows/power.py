"""녹화 중 절전·화면 꺼짐 방지. SetThreadExecutionState는 호출 스레드 단위로 동작한다."""

from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake() -> None:
    if os.name == "nt" and not ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    ):
        log.warning("절전 방지 설정 실패")


def allow_sleep() -> None:
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
