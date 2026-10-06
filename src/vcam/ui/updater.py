"""업데이트 확인·다운로드를 작업 스레드에서 돌리고 결과를 Qt 신호로 돌려준다."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from vcam import __version__
from vcam.services.update_service import (
    ReleaseInfo,
    UpdateError,
    can_write,
    download_release,
    extract_release,
    fetch_latest,
    install_dir,
    is_newer,
    launch_apply,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    staged: Path
    target: Path


class Updater(QObject):
    prepared = Signal(object)  # PreparedUpdate — 설치 준비 완료
    available = Signal(object)  # ReleaseInfo — 새 버전이 있지만 자동 설치 불가(개발 실행, 쓰기 권한 없음)
    up_to_date = Signal(str)  # 최신 버전 (수동 확인일 때만)
    failed = Signal(str)  # 오류 메시지 (수동 확인일 때만)
    progress = Signal(int)  # 다운로드 %

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self.pending: PreparedUpdate | None = None

    def check(self, manual: bool) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._work, args=(manual,), name="vcam-update", daemon=True).start()

    def _work(self, manual: bool) -> None:
        try:
            release = fetch_latest()
            if release is None or not is_newer(release.version):
                log.info("업데이트 없음 (현재 %s, 최신 %s)", __version__, release.version if release else "-")
                if manual:
                    self.up_to_date.emit(__version__)
                return
            target = install_dir()
            if target is None or not can_write(target):
                self.available.emit(release)
                return
            zip_path = download_release(
                release, progress=lambda done, total: self.progress.emit(int(done * 100 / total) if total else 0)
            )
            staged = extract_release(zip_path)
            zip_path.unlink(missing_ok=True)
            self.prepared.emit(PreparedUpdate(release, staged, target))
        except UpdateError as exc:
            log.warning("업데이트 실패: %s", exc)
            if manual:
                self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위: 앱 동작에 영향 주지 않게 기록만
            log.exception("업데이트 중 예기치 않은 오류")
            if manual:
                self.failed.emit(repr(exc))
        finally:
            self._busy = False

    def apply(self, update: PreparedUpdate, restart: bool) -> None:
        launch_apply(update.staged, update.target, restart)
