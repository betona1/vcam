"""업데이트 흐름: 새 버전을 찾으면 먼저 묻고(found), 사용자가 고른 뒤에만 내려받는다(download → prepared)."""

import os
import winreg

import pytest

from vcam.services import update_service as svc
from vcam.services.update_service import ReleaseInfo, UpdateError, sync_installed_version
from vcam.ui.updater import Updater

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows 전용")

RELEASE = ReleaseInfo("99.0.0", "v99.0.0", "변경 내역", "https://example/rel", "zip", "vcam-99.0.0-win64.zip", "sha", 10)


@pytest.fixture
def fake_service(monkeypatch, tmp_path):
    calls = {"download": 0}
    monkeypatch.setattr(svc, "fetch_latest", lambda: RELEASE)
    monkeypatch.setattr(svc, "install_dir", lambda: tmp_path)

    def download(release, progress=None):
        calls["download"] += 1
        progress(5, 10)
        z = tmp_path / release.zip_name
        z.write_bytes(b"zip")
        return z

    staged = tmp_path / "staged"
    staged.mkdir()
    monkeypatch.setattr(svc, "download_release", download)
    monkeypatch.setattr(svc, "extract_release", lambda _z: staged)
    return calls


def test_check_only_announces_and_never_downloads(qtbot, fake_service):
    u = Updater()
    with qtbot.waitSignal(u.found, timeout=5000) as found:
        u.check(manual=False)
    release, installable, manual = found.args
    assert release.version == "99.0.0" and installable and manual is False
    qtbot.waitUntil(lambda: not u.busy, timeout=2000)
    assert fake_service["download"] == 0, "사용자에게 묻기 전에는 내려받지 않는다"


def test_download_after_user_agrees(qtbot, fake_service, tmp_path):
    u = Updater()
    progress = []
    u.progress.connect(progress.append)
    with qtbot.waitSignal(u.prepared, timeout=5000) as prepared:
        assert u.download(RELEASE)
    update = prepared.args[0]
    assert update.release is RELEASE and update.target == tmp_path
    assert fake_service["download"] == 1 and progress == [50]
    assert not (tmp_path / RELEASE.zip_name).exists(), "압축을 푼 zip은 지운다"


def test_not_installable_when_running_from_source(qtbot, fake_service, monkeypatch):
    monkeypatch.setattr(svc, "install_dir", lambda: None)
    u = Updater()
    with qtbot.waitSignal(u.found, timeout=5000) as found:
        u.check(manual=True)
    assert found.args[1] is False
    with qtbot.waitSignal(u.failed, timeout=5000):
        u.download(RELEASE)


def test_up_to_date_only_reported_on_manual_check(qtbot, monkeypatch):
    monkeypatch.setattr(svc, "fetch_latest", lambda: None)
    u = Updater()
    with qtbot.waitSignal(u.up_to_date, timeout=5000):
        u.check(manual=True)
    qtbot.waitUntil(lambda: not u.busy, timeout=2000)
    with qtbot.assertNotEmitted(u.up_to_date, wait=300):
        u.check(manual=False)


def test_check_failure_is_silent_when_automatic(qtbot, monkeypatch):
    def boom():
        raise UpdateError("연결 실패")

    monkeypatch.setattr(svc, "fetch_latest", boom)
    u = Updater()
    with qtbot.assertNotEmitted(u.failed, wait=500):
        u.check(manual=False)
    qtbot.waitUntil(lambda: not u.busy, timeout=2000)
    with qtbot.waitSignal(u.failed, timeout=5000):
        u.check(manual=True)


@pytest.fixture
def uninstall_key():
    path = r"Software\vcam-test\Uninstall\{TEST}_is1"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "0.5.0")
    yield path
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    for parent in (r"Software\vcam-test\Uninstall", r"Software\vcam-test"):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, parent)


def test_sync_installed_version_updates_apps_list(uninstall_key):
    assert sync_installed_version("0.6.0", uninstall_key) is True
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key) as key:
        assert winreg.QueryValueEx(key, "DisplayVersion")[0] == "0.6.0"
    assert sync_installed_version("0.6.0", uninstall_key) is False


def test_sync_installed_version_ignores_zip_installs():
    assert sync_installed_version("0.6.0", r"Software\vcam-test-missing\{NONE}_is1") is False
