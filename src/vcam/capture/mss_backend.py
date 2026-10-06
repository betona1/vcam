"""python-mss(GDI) 기반 캡처. 가장 호환성이 높은 폴백 백엔드."""

from __future__ import annotations

import mss
import numpy as np

from vcam.capture.base import CaptureError
from vcam.capture.mss_compat import mss_factory
from vcam.domain.models import Rect


class MssBackend:
    name = "mss"

    def __init__(self) -> None:
        self._sct: mss.base.MSSBase | None = None
        self._monitor: dict[str, int] | None = None

    def open(self, rect: Rect) -> None:
        try:
            self._sct = mss_factory()
        except mss.exception.ScreenShotError as exc:
            raise CaptureError(f"GDI 캡처를 초기화하지 못했습니다: {exc}") from exc
        self._monitor = {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}

    def grab(self) -> np.ndarray | None:
        if self._sct is None or self._monitor is None:
            raise CaptureError("캡처가 열려 있지 않습니다")
        try:
            shot = self._sct.grab(self._monitor)
        except mss.exception.ScreenShotError as exc:
            raise CaptureError(f"화면을 가져오지 못했습니다: {exc}") from exc
        return np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
        self._sct = None
