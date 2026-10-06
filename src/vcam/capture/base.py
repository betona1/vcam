"""캡처 백엔드 프로토콜과 테스트용 가짜 백엔드.

백엔드는 반드시 같은 스레드에서 open → grab → close 순으로 사용한다.
grab은 (높이, 너비, 4) BGRA uint8 배열을 돌려주며, 새 프레임이 없으면 None을 돌려줄 수 있다.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from vcam.domain.models import Rect


class CaptureError(Exception):
    pass


class CaptureBackend(Protocol):
    name: str

    def open(self, rect: Rect) -> None: ...

    def grab(self) -> np.ndarray | None: ...

    def close(self) -> None: ...


class FakeCaptureBackend:
    """실제 장치 없이 파이프라인을 시험하기 위한 움직이는 그라디언트 프레임."""

    name = "fake"

    def __init__(self, fail_after: int | None = None) -> None:
        self._fail_after = fail_after
        self._count = 0
        self._base: np.ndarray | None = None

    def open(self, rect: Rect) -> None:
        h, w = rect.height, rect.width
        x = np.linspace(0, 255, w, dtype=np.uint16)
        y = np.linspace(0, 255, h, dtype=np.uint16)[:, None]
        base = np.zeros((h, w, 4), dtype=np.uint8)
        base[..., 0] = x[None, :]
        base[..., 1] = y
        base[..., 3] = 255
        self._base = base

    def grab(self) -> np.ndarray | None:
        if self._base is None:
            raise CaptureError("open()을 먼저 호출해야 합니다")
        self._count += 1
        if self._fail_after is not None and self._count > self._fail_after:
            raise CaptureError("가짜 캡처 백엔드 오류")
        frame = self._base.copy()
        frame[..., 2] = (self._count * 8) % 256
        return frame

    def close(self) -> None:
        self._base = None
