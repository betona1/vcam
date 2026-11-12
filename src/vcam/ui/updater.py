"""업데이트 확인·다운로드를 작업 스레드에서 돌리고 결과를 Qt 신호로 돌려준다.

흐름: check() → found(새 버전, 자동 설치 가능 여부) → 사용자가 '업데이트'를 고르면 download() → prepared.
사용자에게 묻기 전에는 아무것도 내려받지 않는다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from vcam import __version__
from vcam.services import update_service as svc
from vcam.services.update_service import ReleaseInfo, UpdateError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedUpdate:
    release: ReleaseInfo
    staged: Path
    target: Path


class Updater(QObject):
    found = Signal(object, bool, bool)  # ReleaseInfo, 자동 설치 가능, 수동 확인
    prepared = Signal(object)  # PreparedUpdate — 내려받아 설치 준비 완료
    up_to_date = Signal(str)  # 최신 버전 (수동 확인일 때만)
    failed = Signal(str)  # 오류 메시지 (수동 확인·다운로드 중일 때만)
    progress = Signal(int)  # 다운로드 %

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self.pending: PreparedUpdate | None = None

    @property
    def busy(self) -> bool:
        return self._busy

    def _run(self, target, *args) -> bool:
        if self._busy:
            return False
        self._busy = True
        threading.Thread(target=self._guard, args=(target, *args), name="vcam-update", daemon=True).start()
        return True

    def _guard(self, target, *args) -> None:
        try:
            target(*args)
        finally:
            self._busy = False

    def check(self, manual: bool) -> None:
        if not self._run(self._check, manual) and manual:
            self.failed.emit("이미 새 버전을 확인하거나 내려받는 중입니다. 잠시 후 다시 시도해 주세요.")

    def download(self, release: ReleaseInfo) -> bool:
        """사용자가 업데이트를 고른 뒤 호출한다. 이미 작업 중이면 False."""
        return self._run(self._download, release)

    def _check(self, manual: bool) -> None:
        try:
            release = svc.fetch_latest()
            if release is None or not svc.is_newer(release.version):
                log.info("업데이트 없음 (현재 %s, 최신 %s)", __version__, release.version if release else "-")
                if manual:
                    self.up_to_date.emit(__version__)
                return
            target = svc.install_dir()
            installable = target is not None and svc.can_write(target)
            log.info("새 버전 %s 발견 (자동 설치 %s)", release.version, "가능" if installable else "불가")
            self.found.emit(release, installable, manual)
        except UpdateError as exc:
            log.warning("업데이트 확인 실패: %s", exc)
            if manual:
                self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위: 앱 동작에 영향 주지 않게 기록만
            log.exception("업데이트 확인 중 예기치 않은 오류")
            if manual:
                self.failed.emit(repr(exc))

    def _download(self, release: ReleaseInfo) -> None:
        try:
            target = svc.install_dir()
            if target is None or not svc.can_write(target):
                raise UpdateError("설치 폴더에 쓸 수 없어 자동으로 업데이트할 수 없습니다")
            zip_path = svc.download_release(
                release, progress=lambda done, total: self.progress.emit(int(done * 100 / total) if total else 0)
            )
            staged = svc.extract_release(zip_path)
            zip_path.unlink(missing_ok=True)
            self.prepared.emit(PreparedUpdate(release, staged, target))
        except UpdateError as exc:
            log.warning("업데이트 다운로드 실패: %s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위
            log.exception("업데이트 다운로드 중 예기치 않은 오류")
            self.failed.emit(repr(exc))

    def apply(self, update: PreparedUpdate, restart: bool) -> None:
        svc.launch_apply(update.staged, update.target, restart)
