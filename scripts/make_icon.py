"""assets/brand/vaveling_face.png(베이블링 5단계 얼굴) → 둥근 사각형 앱 아이콘 vcam.ico(16~256px) / vcam.png"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
FACE = ROOT / "assets" / "brand" / "vaveling_face.png"
ICONS = ROOT / "assets" / "icons"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
MASTER = 1024
RADIUS = 0.22  # 둥근 모서리 비율 (Windows 11 앱 아이콘과 비슷한 정도)
BORDER = (167, 139, 255)  # 은은한 보라 테두리


def rounded_icon(face: Image.Image, size: int) -> Image.Image:
    """큰 캔버스에서 둥근 사각형으로 자르고 테두리를 그린 뒤 줄인다(작은 크기도 가장자리가 매끄럽게)."""
    art = face.convert("RGBA").resize((MASTER, MASTER), Image.LANCZOS)
    mask = Image.new("L", (MASTER, MASTER), 0)
    radius = int(MASTER * RADIUS)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, MASTER - 1, MASTER - 1), radius, fill=255)
    icon = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    icon.paste(art, (0, 0), mask)
    if size >= 32:
        ring = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
        width = MASTER // 48
        ImageDraw.Draw(ring).rounded_rectangle(
            (width // 2, width // 2, MASTER - 1 - width // 2, MASTER - 1 - width // 2),
            radius - width // 2, outline=(*BORDER, 200), width=width,
        )  # fmt: skip
        icon = Image.alpha_composite(icon, ring)
    small = icon.resize((size, size), Image.LANCZOS)
    if size <= 48:
        small = small.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=2))
    return small


def main() -> int:
    face = Image.open(FACE)
    images = [rounded_icon(face, s) for s in SIZES]
    images[-1].save(ICONS / "vcam.png")
    images[-1].save(ICONS / "vcam.ico", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print("vcam.ico / vcam.png 생성 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
