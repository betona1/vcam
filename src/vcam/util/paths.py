"""경로, 파일명 템플릿, 충돌 회피, 디스크 검사."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

APP_DIR_NAME = "vcam"
DEFAULT_FILENAME_TEMPLATE = "vcam_{yyyy-MM-dd}_{HH-mm-ss}"

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_TOKENS = {
    "yyyy-MM-dd": "%Y-%m-%d",
    "HH-mm-ss": "%H-%M-%S",
    "yyyy": "%Y",
    "MM": "%m",
    "dd": "%d",
    "HH": "%H",
    "mm": "%M",
    "ss": "%S",
}


def assets_dir() -> Path:
    """개발 환경(저장소 루트/assets)과 PyInstaller 배포(_MEIPASS/assets)를 모두 지원한다."""
    import sys

    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "assets"
    return Path(__file__).resolve().parents[3] / "assets"


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(exist_ok=True)
    return path


def sessions_dir() -> Path:
    path = app_data_dir() / "sessions"
    path.mkdir(exist_ok=True)
    return path


def cache_dir() -> Path:
    path = app_data_dir() / "cache"
    path.mkdir(exist_ok=True)
    return path


def _known_videos_folder() -> Path | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_Videos {18989B1D-99B5-455B-841C-AB7C74E4DDFC}
    guid = GUID(0x18989B1D, 0x99B5, 0x455B, (ctypes.c_ubyte * 8)(0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC))
    out = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value) if out.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def default_output_dir() -> Path:
    videos = _known_videos_folder() or Path.home() / "Videos"
    return videos / APP_DIR_NAME


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_CHARS.sub("_", name).strip().rstrip(".")
    if not cleaned:
        cleaned = "vcam"
    if cleaned.upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned[:150]


def render_filename(template: str, now: datetime) -> str:
    def replace(match: re.Match[str]) -> str:
        fmt = _TOKENS.get(match.group(1))
        return now.strftime(fmt) if fmt else match.group(0)

    return sanitize_filename(re.sub(r"\{([^{}]+)\}", replace, template or DEFAULT_FILENAME_TEMPLATE))


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """기존 파일을 덮어쓰지 않도록 `이름 (2).mp4` 형태로 비어 있는 경로를 찾는다."""
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".vcam-write-test-", delete=True):
            pass
        return True
    except OSError:
        return False


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def redact_path(path: Path | str) -> str:
    """로그에 전체 사용자 경로를 남기지 않도록 홈 디렉터리를 ~로 줄인다."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.lower().startswith(home.lower()) else text


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
