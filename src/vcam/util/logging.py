"""회전 로그 파일 설정. 로그는 %LOCALAPPDATA%\\vcam\\logs 에 남는다."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from vcam.util.paths import logs_dir

_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    handler = RotatingFileHandler(
        logs_dir() / "vcam.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    if debug or sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)
    logging.getLogger("comtypes").setLevel(logging.WARNING)
