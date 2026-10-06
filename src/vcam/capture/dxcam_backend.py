"""DXcam(DXGI Desktop Duplication) 기반 고성능 캡처.

캡처 영역이 한 모니터 안에 완전히 들어갈 때만 사용한다. 그렇지 않으면 CaptureError를
던지고, 호출자는 mss로 폴백한다.
"""

from __future__ import annotations

import logging

import numpy as np

from vcam.capture.base import CaptureError
from vcam.domain.models import Rect

log = logging.getLogger(__name__)


def _find_output(dxcam_mod, rect: Rect) -> tuple[int, int, Rect]:
    factory = dxcam_mod.__dict__["__factory"]
    for d_idx, outputs in enumerate(factory.outputs):
        for o_idx, output in enumerate(outputs):
            output.update_desc()
            c = output.desc.DesktopCoordinates
            out_rect = Rect(c.left, c.top, c.right - c.left, c.bottom - c.top)
            if out_rect.contains_rect(rect):
                if output.rotation_angle not in (0,):
                    raise CaptureError("회전된 모니터는 DXcam 대신 GDI 캡처를 사용합니다")
                return d_idx, o_idx, out_rect
    raise CaptureError("선택 영역이 한 모니터 안에 있지 않아 DXcam을 사용할 수 없습니다")


class DxcamBackend:
    name = "dxcam"

    def __init__(self) -> None:
        self._camera = None
        self._region: tuple[int, int, int, int] | None = None
        self._last: np.ndarray | None = None

    def open(self, rect: Rect) -> None:
        try:
            import dxcam
        except Exception as exc:  # noqa: BLE001 - 선택 의존성 로딩 실패는 폴백 사유
            raise CaptureError(f"DXcam을 불러오지 못했습니다: {exc}") from exc
        d_idx, o_idx, out_rect = _find_output(dxcam, rect)
        try:
            self._camera = dxcam.create(
                device_idx=d_idx, output_idx=o_idx, output_color="BGRA", processor_backend="numpy"
            )
        except Exception as exc:  # noqa: BLE001 - COM/DXGI 오류 종류가 다양함
            raise CaptureError(f"DXcam 카메라를 만들지 못했습니다: {exc}") from exc
        self._region = (
            rect.left - out_rect.left,
            rect.top - out_rect.top,
            rect.right - out_rect.left,
            rect.bottom - out_rect.top,
        )
        # 첫 프레임을 실제로 받아 동작 여부를 확인한다.
        first = self.grab()
        if first is None:
            self.close()
            raise CaptureError("DXcam이 첫 프레임을 돌려주지 않았습니다")

    def grab(self) -> np.ndarray | None:
        if self._camera is None:
            raise CaptureError("캡처가 열려 있지 않습니다")
        try:
            frame = self._camera.grab(region=self._region, new_frame_only=False)
        except Exception as exc:  # noqa: BLE001 - 장치 분리, 모드 변경 등
            raise CaptureError(f"DXcam 캡처 오류: {exc}") from exc
        if frame is None:
            return self._last
        self._last = np.ascontiguousarray(frame)
        return self._last

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:  # noqa: BLE001
                log.warning("DXcam 해제 중 오류", exc_info=True)
            del self._camera
        self._camera = None
        self._last = None
