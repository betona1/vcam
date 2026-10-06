"""디자인 토큰. 브랜드 색은 5단계 바브링의 보라 오라와 청록 렌즈에서 가져왔다."""

from __future__ import annotations

from dataclasses import dataclass

SPACE = (4, 8, 12, 16, 24, 32)
RADIUS_CONTROL = 6
RADIUS_CARD = 10
FONT_FAMILY = "Segoe UI Variable Text"
FONT_FALLBACK = "Segoe UI"


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    cyan: str
    rec: str
    ok: str
    warn: str
    dim: str  # 영역 선택 오버레이 딤


DARK = Palette(
    name="dark",
    bg="#0f1226",
    surface="#171b36",
    surface2="#20254a",
    border="#2e3463",
    text="#eceeff",
    muted="#9ba1cb",
    accent="#8b6bff",
    accent_hover="#a189ff",
    accent_text="#ffffff",
    cyan="#4fd8f0",
    rec="#ff4d6a",
    ok="#3fd29b",
    warn="#f5b942",
    dim="#080a1e",
)

LIGHT = Palette(
    name="light",
    bg="#f3f4fb",
    surface="#ffffff",
    surface2="#eceefa",
    border="#d7daee",
    text="#191c38",
    muted="#5a6087",
    accent="#5b3fe0",
    accent_hover="#4a2fcc",
    accent_text="#ffffff",
    cyan="#0a87a8",
    rec="#d42a49",
    ok="#12825a",
    warn="#9a6200",
    dim="#080a1e",
)
