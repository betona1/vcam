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
### 설치
1. 아래 **Assets**에서 **`vcam-setup-{v}.exe`** 를 내려받아 실행합니다. 관리자 권한 없이 설치되고 시작 메뉴에 vCAM이 생깁니다.
   - "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**을 누르세요 (코드 서명 전이라 나오는 경고입니다).
   - 설치 없이 쓰려면 `vcam-{v}-win64.zip`을 풀어 `vcam\\vcam.exe`를 실행해도 됩니다.
2. **FFmpeg**가 필요합니다. 명령 프롬프트에서 한 번만: `winget install Gyan.FFmpeg`
3. vCAM을 열고 **화면** 또는 **영역**을 고른 뒤 **F9**(녹화 시작/종료), **F10**(일시정지).

### 업데이트
이미 설치했다면 vCAM을 시작할 때 "업데이트가 있습니다. 업데이트하시겠습니까?"가 뜹니다. **업데이트**를 누르면 내려받아 다시 시작하며 설치합니다.

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
