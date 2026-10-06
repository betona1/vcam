"""CHANGELOG.md에서 해당 버전 절을 뽑아 릴리스 노트로 출력한다.

사용: python scripts/release_notes.py 0.2.0
      python scripts/release_notes.py --app-version   (src/vcam/__init__.py의 버전 출력)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOOTER = """
---
**설치:** `vcam-{v}-win64.zip`을 내려받아 압축을 풀고 `vcam\\vcam.exe`를 실행하세요. FFmpeg가 필요합니다(`winget install Gyan.FFmpeg`).
이미 설치했다면 앱이 시작할 때 자동으로 새 버전을 받아 설치를 안내합니다.
자세한 사용법: [사용설명서](https://github.com/betona1/vcam/blob/main/docs/USER_GUIDE.md)
"""


def section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[?v?{re.escape(version)}\]?.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else f"vCAM v{version}"


def app_version() -> str:
    text = (ROOT / "src" / "vcam" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


if __name__ == "__main__":
    if sys.argv[1] == "--app-version":
        print(app_version())
        raise SystemExit(0)
    v = sys.argv[1].lstrip("v")
    sys.stdout.reconfigure(encoding="utf-8")
    print(section(v) + "\n" + FOOTER.format(v=v))
