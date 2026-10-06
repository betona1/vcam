import hashlib
import io
import json
import subprocess
import zipfile

import pytest

from vcam.services import update_service as us

REPO = "betona1/vcam"
BASE = f"https://github.com/{REPO}/releases/download/v0.3.0/"


def release_json(**over):
    data = {
        "tag_name": "v0.3.0",
        "body": "변경 사항",
        "html_url": f"https://github.com/{REPO}/releases/tag/v0.3.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "vcam-0.3.0-win64.zip", "browser_download_url": BASE + "vcam-0.3.0-win64.zip", "size": 10},
            {"name": "vcam-0.3.0-win64.zip.sha256", "browser_download_url": BASE + "vcam-0.3.0-win64.zip.sha256"},
        ],
    }
    data.update(over)
    return data


def test_version_compare():
    assert us.parse_version("v1.2.3") == (1, 2, 3)
    assert us.parse_version("0.2") == (0, 2, 0)
    assert us.parse_version("1.0.0-beta") == (1, 0, 0)
    assert us.is_newer("v0.10.0", "0.9.9")
    assert not us.is_newer("0.2.0", "0.2.0")
    assert not us.is_newer("v0.1.9", "0.2.0")


def test_parse_release_picks_zip_and_checksum():
    r = us.parse_release(release_json(), REPO)
    assert r.version == "0.3.0" and r.zip_name == "vcam-0.3.0-win64.zip"
    assert r.sha256_url.endswith(".sha256")


def test_release_without_checksum_is_not_installed():
    data = release_json()
    data["assets"] = data["assets"][:1]
    assert us.parse_release(data, REPO) is None


def test_prerelease_and_draft_ignored():
    assert us.parse_release(release_json(prerelease=True), REPO) is None
    assert us.parse_release(release_json(draft=True), REPO) is None


def test_foreign_download_url_rejected():
    data = release_json()
    data["assets"][0]["browser_download_url"] = "https://evil.example.com/vcam-0.3.0-win64.zip"
    with pytest.raises(us.UpdateError):
        us.parse_release(data, REPO)


def test_fetch_latest_handles_404(monkeypatch):
    import urllib.error

    def fetch(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    assert us.fetch_latest(REPO, fetch) is None


def test_fetch_latest_parses(monkeypatch):
    r = us.fetch_latest(REPO, lambda url, t: json.dumps(release_json()).encode())
    assert r is not None and r.tag == "v0.3.0"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def make_opener(payload: bytes, checksum: str):
    def opener(request, timeout):
        return FakeResponse(checksum.encode() if request.full_url.endswith(".sha256") else payload)

    return opener


def test_download_verifies_checksum(tmp_path):
    payload = b"zip-bytes" * 1000
    rel = us.parse_release(release_json(), REPO)
    good = hashlib.sha256(payload).hexdigest() + "  vcam-0.3.0-win64.zip\n"
    path = us.download_release(rel, tmp_path, opener=make_opener(payload, good))
    assert path.read_bytes() == payload

    bad_dir = tmp_path / "x"
    bad_dir.mkdir()
    with pytest.raises(us.UpdateError):
        us.download_release(rel, bad_dir, opener=make_opener(payload, "0" * 64))
    assert not list(bad_dir.iterdir()), "검증 실패 파일은 남기지 않는다"


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_extract_finds_app_folder(tmp_path):
    z = tmp_path / "a.zip"
    make_zip(z, {"vcam/vcam.exe": b"exe", "vcam/_internal/lib.dll": b"dll"})
    app = us.extract_release(z, tmp_path / "staged")
    assert (app / "vcam.exe").read_bytes() == b"exe"


def test_extract_blocks_path_traversal(tmp_path):
    z = tmp_path / "evil.zip"
    make_zip(z, {"vcam/vcam.exe": b"exe", "../../escape.txt": b"x"})
    with pytest.raises(us.UpdateError):
        us.extract_release(z, tmp_path / "staged")
    assert not (tmp_path / "escape.txt").exists()


def test_apply_script_replaces_files_and_cleans_up(tmp_path):
    """실제 PowerShell 스크립트로 설치 폴더 교체를 검증한다(이미 끝난 프로세스 PID 사용)."""
    target = tmp_path / "install"
    (target / "_internal").mkdir(parents=True)
    (target / "vcam.exe").write_bytes(b"old-exe")
    (target / "_internal" / "old.dll").write_bytes(b"old")
    (target / "user-note.txt").write_text("keep")
    staged = tmp_path / "staged" / "vcam"
    (staged / "_internal").mkdir(parents=True)
    (staged / "vcam.exe").write_bytes(b"new-exe")
    (staged / "_internal" / "new.dll").write_bytes(b"new")
    script = us.write_apply_script()
    dead = subprocess.Popen(["cmd", "/c", "exit"]).pid
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-ProcessId", str(dead), "-Staged", str(staged), "-Target", str(target),
         "-Log", str(tmp_path / "update.log"), "-Restart", "0"],
        check=True, timeout=120,
    )  # fmt: skip
    assert (target / "vcam.exe").read_bytes() == b"new-exe"
    assert (target / "_internal" / "new.dll").exists()
    assert not (target / "_internal" / "old.dll").exists(), "_internal은 통째로 교체된다"
    assert (target / "user-note.txt").read_text() == "keep", "앱 파일이 아닌 것은 건드리지 않는다"
    assert not list(target.glob(".vcam-backup-*")), "성공하면 백업을 지운다"
    assert "update applied" in (tmp_path / "update.log").read_text(encoding="utf-8-sig")


def test_install_dir_none_in_dev():
    assert us.install_dir() is None
