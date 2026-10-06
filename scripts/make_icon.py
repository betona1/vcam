"""assets/icons/vcam.svg → vcam.ico(16~256px) / vcam.png 생성."""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "icons" / "vcam.svg"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(renderer: QSvgRenderer, size: int) -> Image.Image:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def main() -> int:
    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        print("SVG를 읽지 못했습니다", file=sys.stderr)
        return 1
    images = [render(renderer, s) for s in SIZES]
    images[-1].save(ROOT / "assets" / "icons" / "vcam.png")
    images[-1].save(ROOT / "assets" / "icons" / "vcam.ico", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print("vcam.ico / vcam.png 생성 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
