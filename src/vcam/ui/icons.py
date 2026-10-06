"""vcam 전용 라인 아이콘 세트(24×24, 직접 제작). 테마 색으로 칠해 QIcon을 만든다."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS: dict[str, str] = {
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "region": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>'
              '<rect x="8.5" y="8.5" width="7" height="7" rx="1" stroke-dasharray="2 1.6"/>',
    "record": '<circle cx="12" cy="12" r="6.5" fill="{c}" stroke="none"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2.2" fill="{c}" stroke="none"/>',
    "pause": '<rect x="6.5" y="5" width="3.6" height="14" rx="1.2" fill="{c}" stroke="none"/>'
             '<rect x="13.9" y="5" width="3.6" height="14" rx="1.2" fill="{c}" stroke="none"/>',
    "play": '<path d="M8 5.5v13a.8.8 0 0 0 1.2.7l10.4-6.5a.8.8 0 0 0 0-1.4L9.2 4.8A.8.8 0 0 0 8 5.5z" fill="{c}" stroke="none"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "folder_edit": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7V12"/><path d="M3 7.5v10A2.5 2.5 0 0 0 5.5 20H11"/>'
                   '<path d="M14 20l.6-2.6 4.6-4.6a1.4 1.4 0 0 1 2 2l-4.6 4.6z"/>',
    "trash": '<path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10 11v5.5M14 11v5.5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.3c-.7.3-1.2.9-1.2 1.6v.6"/><circle cx="12" cy="16.9" r=".9" fill="{c}" stroke="none"/>',
    "pulse": '<path d="M3 12h4l2.2-5.5 4.2 11 2.3-5.5H21"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4.5V9h-4.5"/>',
    "gauge": '<path d="M4.5 17a8 8 0 1 1 15 0"/><path d="M12 15l4-4.5"/><circle cx="12" cy="15.5" r="1.3" fill="{c}" stroke="none"/>',
    "sparkle": '<path d="M12 3.5l2.2 5.2 5.3 2.3-5.3 2.3L12 18.5l-2.2-5.2L4.5 11l5.3-2.3z"/><path d="M18.5 16.5v4M16.5 18.5h4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
    "resize": '<path d="M14.5 4H20v5.5M9.5 20H4v-5.5M20 4l-6.5 6.5M4 20l6.5-6.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "alert": '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.2" r=".9" fill="{c}" stroke="none"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r=".9" fill="{c}" stroke="none"/>',
    "film": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M8 4.5v15M16 4.5v15M3.5 9.5H8M3.5 14.5H8M16 9.5h4.5M16 14.5h4.5"/>',
    "lifebuoy": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.8"/><path d="M5.6 5.6l3.7 3.7M14.7 14.7l3.7 3.7M18.4 5.6l-3.7 3.7M9.3 14.7l-3.7 3.7"/>',
    "log": '<path d="M7 3.5h7l4.5 4.5v12.5H7z"/><path d="M14 3.5V8h4.5M9.5 12.5h6M9.5 16h6"/>',
    "guide": '<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><path d="M4 12h2M18 12h2M12 4v2M12 18v2"/>',
    "theme": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="{c}" stroke="none"/>',
    "grip": '<circle cx="9" cy="7" r="1.3" fill="{c}" stroke="none"/><circle cx="15" cy="7" r="1.3" fill="{c}" stroke="none"/>'
            '<circle cx="9" cy="12" r="1.3" fill="{c}" stroke="none"/><circle cx="15" cy="12" r="1.3" fill="{c}" stroke="none"/>'
            '<circle cx="9" cy="17" r="1.3" fill="{c}" stroke="none"/><circle cx="15" cy="17" r="1.3" fill="{c}" stroke="none"/>',
    "fullscreen": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7.5 14h9"/>',
    "speaker": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "speaker_off": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    "mic": '<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"/>',
    "mic_off": '<path d="M15 10V6.5a3 3 0 0 0-5.6-1.5M9 9v2.5a3 3 0 0 0 4.8 2.4"/><path d="M5.5 11.5a6.5 6.5 0 0 0 10.8 4.9M18.5 11.5a6.4 6.4 0 0 1-.5 2.5M12 18v3M9 21h6M4 4l16 16"/>',
    "exit":'<path d="M14 4.5h4.5a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H14"/><path d="M10 8l-4 4 4 4M6 12h9.5"/>',
}

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)


def svg_for(name: str, color: str) -> bytes:
    body = _PATHS[name].replace("{c}", color)
    return _TEMPLATE.format(c=color, body=body).encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int, dpr: float = 1.0) -> QPixmap:
    px = max(1, round(size * dpr))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg_for(name, color))).render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm = QPixmap.fromImage(image)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, disabled_color: str | None = None, active_color: str | None = None) -> QIcon:
    result = QIcon()
    for size in (16, 20, 24, 32, 48):
        for dpr in (1.0, 2.0):
            result.addPixmap(pixmap(name, color, size, dpr), QIcon.Mode.Normal)
            if disabled_color:
                result.addPixmap(pixmap(name, disabled_color, size, dpr), QIcon.Mode.Disabled)
            if active_color:
                result.addPixmap(pixmap(name, active_color, size, dpr), QIcon.Mode.Normal, QIcon.State.On)
    return result
