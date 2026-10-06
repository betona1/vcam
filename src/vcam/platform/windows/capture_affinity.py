"""vcam 자신의 가이드 프레임·컨트롤 바가 녹화 결과에 찍히지 않게 한다.

WDA_EXCLUDEFROMCAPTURE(Windows 10 2004+)는 해당 창을 화면 캡처에서 제외할 뿐, 사용자 화면에서는
그대로 보인다. 지원되지 않는 OS에서는 아무 일도 하지 않으며, 그 경우 프레임은 녹화 영역 바깥에만 그려진다.
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(win_id: int) -> bool:
    try:
        ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(win_id), WDA_EXCLUDEFROMCAPTURE))
    except OSError:
        ok = False
    if not ok:
        log.debug("캡처 제외 설정 미지원 (hwnd=%s)", win_id)
    return ok
