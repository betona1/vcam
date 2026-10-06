"""캡처 백엔드 선택과 폴백."""

from __future__ import annotations

import logging

from vcam.capture.base import CaptureBackend, CaptureError
from vcam.domain.models import Rect

log = logging.getLogger(__name__)


def open_capture_backend(preference: str, rect: Rect) -> CaptureBackend:
    """선호 백엔드를 열고, 실패하면 GDI(mss)로 폴백한다. 반드시 캡처 스레드에서 호출한다."""
    from vcam.capture.mss_backend import MssBackend

    if preference in ("auto", "dxcam"):
        from vcam.capture.dxcam_backend import DxcamBackend

        backend = DxcamBackend()
        try:
            backend.open(rect)
            return backend
        except CaptureError as exc:
            log.info("DXcam 사용 불가, GDI로 폴백: %s", exc)
        except Exception as exc:  # noqa: BLE001 - DXcam/COM 내부 오류도 폴백 사유
            log.warning("DXcam 오류, GDI로 폴백: %r", exc)
            backend.close()
    backend = MssBackend()
    backend.open(rect)
    return backend
