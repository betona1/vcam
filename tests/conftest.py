import pytest

from vcam.encoding.ffmpeg import find_ffmpeg


@pytest.fixture(scope="session")
def ffmpeg():
    paths = find_ffmpeg()
    if paths is None:
        pytest.skip("FFmpeg가 없어 건너뜁니다")
    return paths


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """테스트가 실제 %LOCALAPPDATA%\\vcam 을 건드리지 않게 한다."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
