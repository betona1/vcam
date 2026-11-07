"""실제 인터넷으로 짧은 영상을 내려받는 스모크 테스트. VCAM_ONLINE_TESTS=1 일 때만 돈다."""

from __future__ import annotations

import os

import pytest

from vcam.download import engine
from vcam.download.engine import DownloadOptions

pytestmark = pytest.mark.smoke

TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
# yt-dlp 자체 테스트에 쓰이는 공개 게시물. 지워지면 바꾼다.
TIKTOK_URL = "https://www.tiktok.com/@/video/7668090902816017671/"
INSTAGRAM_URL = "https://www.instagram.com/reels/Cop84x6u7CP/"


@pytest.fixture
def online():
    if os.environ.get("VCAM_ONLINE_TESTS") != "1":
        pytest.skip("VCAM_ONLINE_TESTS=1 로 켜야 실행됩니다 (인터넷 사용)")
    if not engine.availability().ok:
        pytest.skip("yt-dlp 없음")


def test_probe_and_download_360p_with_subtitles(online, ffmpeg, tmp_path):
    rt = engine.find_js_runtime()
    options = DownloadOptions(kind="video", quality="360", subtitles=True, subtitle_langs=("en.*",), output_dir=str(tmp_path))
    cand = engine.probe(TEST_URL, options, rt)
    assert cand.title and cand.duration_s > 60 and cand.thumbnail
    seen = []
    result = engine.download(TEST_URL, options, ffmpeg, progress=seen.append, log_fn=lambda t: None, js_runtime=rt)
    assert len(result.files) == 1 and result.files[0].suffix == ".mp4" and result.files[0].stat().st_size > 1_000_000
    assert any(p.name.endswith(".en.srt") for p in result.subtitles)
    assert any(p.stage == "download" and p.total for p in seen) and seen[-1].stage == "done"
    from vcam.encoding.validator import probe_media

    info = probe_media(ffmpeg, result.files[0])
    assert info.height <= 360 and info.audio_codec


@pytest.mark.parametrize("url", [TIKTOK_URL, INSTAGRAM_URL])
def test_tiktok_and_instagram_public_video(online, ffmpeg, tmp_path, url):
    """브라우저 위장(curl_cffi)이 있어야 한다. 공개 영상은 로그인 없이 영상+소리 MP4로 받힌다."""
    if not engine.impersonation_available().ok:
        pytest.skip("curl_cffi 없음")
    options = DownloadOptions(kind="video", quality="best", output_dir=str(tmp_path))
    cand = engine.probe(url, options)
    assert cand.title and cand.thumbnail and not cand.is_playlist
    result = engine.download(url, options, ffmpeg, log_fn=lambda t: None)
    assert len(result.files) == 1 and result.files[0].suffix == ".mp4" and result.files[0].stat().st_size > 100_000
    assert result.duration_s > 1
    from vcam.encoding.validator import probe_media

    info = probe_media(ffmpeg, result.files[0])
    assert info.height >= 540 and info.audio_codec and info.duration_s > 1


def test_audio_only_mp3(online, ffmpeg, tmp_path):
    options = DownloadOptions(kind="audio", audio_format="mp3", output_dir=str(tmp_path))
    result = engine.download(TEST_URL, options, ffmpeg, js_runtime=engine.find_js_runtime())
    assert len(result.files) == 1 and result.files[0].suffix == ".mp3" and result.files[0].stat().st_size > 500_000
