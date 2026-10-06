"""애플리케이션 진입점: 로깅, 단일 실행 보장, 테마, 메인 창."""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from vcam import __version__
from vcam.services.profile_service import ProfileService
from vcam.util.logging import setup_logging
from vcam.util.paths import app_data_dir, assets_dir

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vcam", description="vCAM 화면 녹화")
    parser.add_argument("--debug", action="store_true", help="자세한 로그를 남깁니다")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    setup_logging(debug=args.debug)
    log.info("vCAM %s 시작 (Python %s)", __version__, sys.version.split()[0])
    if os.name == "nt":
        # 작업 표시줄에서 python.exe가 아닌 vcam 아이콘으로 묶이도록 한다.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vaveling.vcam")

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("vCAM")
    app.setApplicationDisplayName("vCAM")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("vaveling")
    app.setWindowIcon(QIcon(str(assets_dir() / "icons" / "vcam.ico")))

    lock = QLockFile(str(app_data_dir() / "vcam.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "vCAM", "vCAM이 이미 실행 중입니다.")
        return 0

    from vcam.ui.main_window import MainWindow
    from vcam.ui.theme import apply_theme

    profiles = ProfileService()
    settings = profiles.load()
    palette = apply_theme(app, settings.theme)
    window = MainWindow(profiles, settings, palette)
    window.show()
    code = app.exec()
    lock.unlock()
    log.info("vCAM 종료 (코드 %s)", code)
    return code
