"""Win32 RegisterHotKey 기반 전역 단축키. 모든 키 입력을 훅킹하지 않는다."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
VK = {f"F{i}": 0x6F + i for i in range(1, 13)}


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner: HotkeyManager) -> None:
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._owner._dispatch(int(msg.wParam))
        return False, 0


class HotkeyManager(QObject):
    activated = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ids: dict[int, str] = {}
        self._next_id = 0xB100
        self._filter = _Filter(self)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def register(self, name: str, key: str, modifiers: int = 0) -> bool:
        vk = VK.get(key.upper())
        if vk is None:
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        if not ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
            log.warning("전역 단축키 %s(%s) 등록 실패 — 다른 프로그램이 사용 중일 수 있습니다", key, name)
            return False
        self._ids[hotkey_id] = name
        log.info("전역 단축키 등록: %s → %s", key, name)
        return True

    def unregister_all(self) -> None:
        for hotkey_id in list(self._ids):
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._ids.clear()

    def _dispatch(self, hotkey_id: int) -> None:
        name = self._ids.get(hotkey_id)
        if name:
            self.activated.emit(name)
