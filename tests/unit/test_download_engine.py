"""yt-dlp 어댑터의 순수 함수: 주소 추출, 포맷 선택, 옵션 조립, 오류 문구, 결과 수집 (네트워크·yt-dlp 불필요)."""

from __future__ import annotations

from pathlib import Path

from vcam.download import engine
from vcam.download.engine import DownloadOptions, DownloadResult, build_ydl_options, format_selector
from vcam.encoding.ffmpeg import FfmpegPaths


def test_extract_urls_keeps_order_and_drops_duplicates_and_junk():
    text = "보기: https://youtu.be/abc, https://www.youtube.com/watch?v=xyz&list=PL1\nhttps://youtu.be/abc\n텍스트만"
    assert engine.extract_urls(text) == ["https://youtu.be/abc", "https://www.youtube.com/watch?v=xyz&list=PL1"]
    assert engine.extract_urls("") == []


def test_is_probable_url():
    assert engine.is_probable_url(" https://youtu.be/abc ")
    assert not engine.is_probable_url("youtu.be/abc")
    assert not engine.is_probable_url("https://a.b\nhttps://c.d")
    assert not engine.is_probable_url("")


def test_format_selector_by_kind_and_quality():
    assert format_selector(DownloadOptions(kind="audio")) == "ba/b"
    assert format_selector(DownloadOptions(quality="best")) == "bv*+ba/b"
    assert format_selector(DownloadOptions(quality="720")) == "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
    assert format_selector(DownloadOptions(quality="weird")) == "bv*+ba/b"


def test_build_video_options(tmp_path):
    ff = FfmpegPaths(tmp_path / "bin" / "ffmpeg.exe", tmp_path / "bin" / "ffprobe.exe")
    o = DownloadOptions(kind="video", quality="1080", output_dir=str(tmp_path / "out"))
    opts = build_ydl_options(o, ff, ("deno", Path("C:/deno/deno.exe")))
    assert opts["format"].startswith("bv*[height<=1080]")
    # 추출기 우선순위(TikTok 워터마크 포맷 뒤로)가 해상도보다 먼저, 같은 화질이면 H.264/AAC
    assert opts["format_sort"][:2] == ["ie_pref", "res"] and "vcodec:h264" in opts["format_sort"]
    assert "cookiefile" not in opts and "cookiesfrombrowser" not in opts
    assert opts["merge_output_format"] == "mp4"
    assert opts["ffmpeg_location"] == str(tmp_path / "bin")
    assert opts["js_runtimes"] == {"deno": {"path": "C:\\deno\\deno.exe"}} or opts["js_runtimes"] == {"deno": {"path": "C:/deno/deno.exe"}}
    assert opts["noplaylist"] is True and opts["overwrites"] is False and opts["windowsfilenames"] is True
    assert str(tmp_path / "out") in opts["outtmpl"]["default"]
    assert "%(title)" in opts["outtmpl"]["default"] and "playlist_index" in opts["outtmpl"]["default"]
    assert "writesubtitles" not in opts
    assert [p["key"] for p in opts["postprocessors"]] == ["FFmpegVideoRemuxer"]


def test_build_audio_and_subtitle_and_playlist_options(tmp_path):
    o = DownloadOptions(kind="audio", audio_format="mp3", subtitles=True, auto_subtitles=True,
                        subtitle_langs=("ko.*", "en.*"), playlist=True, output_dir=str(tmp_path))  # fmt: skip
    opts = build_ydl_options(o, None)
    assert "ffmpeg_location" not in opts and "js_runtimes" not in opts and "merge_output_format" not in opts
    assert opts["noplaylist"] is False and opts["ignoreerrors"] == "only_download"
    keys = [p["key"] for p in opts["postprocessors"]]
    assert keys == ["FFmpegExtractAudio", "FFmpegSubtitlesConvertor"]
    assert opts["postprocessors"][0]["preferredcodec"] == "mp3"
    assert opts["writesubtitles"] is True and opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["ko.*", "en.*"] and opts["subtitlesformat"] == "srt/best"


def test_cookie_options_map_to_yt_dlp(tmp_path):
    assert engine.cookie_options(DownloadOptions()) == {}
    assert engine.cookie_options(DownloadOptions(cookies_browser="firefox")) == {"cookiesfrombrowser": ("firefox",)}
    assert engine.cookie_options(DownloadOptions(cookies_browser="file", cookies_file="C:/c.txt")) == {"cookiefile": "C:/c.txt"}
    assert engine.cookie_options(DownloadOptions(cookies_browser="file", cookies_file="")) == {}
    # 정보 읽기와 내려받기 양쪽에 들어간다 (로그인 전용 영상은 정보 읽기부터 막히므로)
    o = DownloadOptions(cookies_browser="edge", output_dir=str(tmp_path))
    assert build_ydl_options(o, None)["cookiesfrombrowser"] == ("edge",)
    assert engine._probe_options(o, None)["cookiesfrombrowser"] == ("edge",)


def test_friendly_error_messages(monkeypatch):
    f = engine._friendly_error
    assert "지원하지 않는 주소" in f("ERROR: Unsupported URL: https://x")
    assert "비공개" in f("ERROR: [youtube] abc: Private video. Sign in if you've been granted access")
    assert "찾을 수 없거나" in f("ERROR: [youtube] abc: Video unavailable")
    assert "화질" in f("ERROR: Requested format is not available")
    assert "FFmpeg" in f("ERROR: ffmpeg not found")
    assert "인터넷 연결" in f("ERROR: Unable to download webpage: <urlopen error timed out>")
    assert f("ERROR: something else") == "something else"
    # 사이트별 문구: 지역 차단, 로그인(TikTok·Instagram·YouTube 표현), 사진 게시물, 요청 제한
    assert "지역" in f("ERROR: [TikTok] 123: Your IP address is blocked from accessing this post")
    for msg in (
        "ERROR: [TikTok] 1: TikTok is requiring login for access to this content. Use --cookies-from-browser",
        "ERROR: [Instagram] abc: You need to log in to access this content",
        "ERROR: [TikTok] 1: This post may not be comfortable for some audiences. Log in for access",
        "ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.",
    ):
        assert "로그인 쿠키" in f(msg), msg
    assert "받을 영상이 없습니다" in f("ERROR: [Instagram] abc: No video formats found!")
    assert "요청을 제한" in f("ERROR: Unable to download JSON metadata: HTTP Error 429: Too Many Requests")
    # webpage 안의 age 와 Confirm you are on the latest version 을 로그인 요구로 오인하지 않는다
    bot = "ERROR: [TikTok] 1: Unexpected response from webpage request; please report this issue ... Confirm you are on the latest version"
    monkeypatch.setattr(engine, "impersonation_available", lambda: engine.Availability(False, "x"))
    assert "curl_cffi" in f(bot) and "로그인" not in f(bot)
    monkeypatch.setattr(engine, "impersonation_available", lambda: engine.Availability(True))
    assert "자동 접속" in f(bot)


def test_impersonation_available_reports_missing_curl_cffi(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "yt_dlp.networking._curlcffi", None)  # import 시 ImportError
    a = engine.impersonation_available()
    assert not a.ok and "curl_cffi" in a.reason


def test_duration_of_falls_back_to_ffprobe(monkeypatch, tmp_path):
    ff = FfmpegPaths(tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")
    f = tmp_path / "a.mp4"
    assert engine._duration_of({"duration": 12.5}, [f], ff) == 12.5
    assert engine._duration_of({"_type": "playlist", "entries": []}, [f], ff) == 0.0
    assert engine._duration_of({"id": "x"}, [f], None) == 0.0
    assert engine._duration_of(None, [f], ff) == 0.0
    from vcam.encoding import validator

    fake = validator.MediaInfo(path=f, duration_s=7.25, width=2, height=2, codec="h264", size_bytes=1)
    monkeypatch.setattr(validator, "probe_media", lambda ffmpeg, path: fake)
    assert engine._duration_of({"id": "x"}, [f], ff) == 7.25

    def boom(ffmpeg, path):
        raise validator.ValidationError("영상 스트림이 없습니다")

    monkeypatch.setattr(validator, "probe_media", boom)
    assert engine._duration_of({"id": "x"}, [f], ff) == 0.0


def test_collect_from_info_handles_single_and_playlist(tmp_path):
    r = DownloadResult()
    engine._collect_from_info({"requested_downloads": [{"filepath": str(tmp_path / "a.mp4")}],
                               "requested_subtitles": {"ko": {"filepath": str(tmp_path / "a.ko.srt")}}}, r)  # fmt: skip
    assert r.files == [tmp_path / "a.mp4"] and r.subtitles == [tmp_path / "a.ko.srt"]
    r = DownloadResult()
    engine._collect_from_info({"_type": "playlist", "entries": [
        {"requested_downloads": [{"filepath": str(tmp_path / "1.mp4")}]}, None,
        {"requested_downloads": [{"filepath": str(tmp_path / "2.mp4")}]}]}, r)  # fmt: skip
    assert [p.name for p in r.files] == ["1.mp4", "2.mp4"] and len(r.errors) == 1
    engine._collect_from_info(None, r)


def test_dedupe_is_case_insensitive_on_windows_paths():
    paths = [Path("C:/v/A.mp4"), Path("c:/v/a.mp4"), Path("C:/v/b.mp4")]
    assert [p.name for p in engine._dedupe(paths)] == ["A.mp4", "b.mp4"]


def test_availability_reports_missing_package(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    a = engine.availability()
    assert not a.ok and "yt-dlp" in a.reason


def test_find_js_runtime_prefers_bundled_deno(tmp_path, monkeypatch):
    deno = tmp_path / "localappdata" / "vcam" / "deno" / "deno.exe"
    deno.parent.mkdir(parents=True)
    deno.write_bytes(b"")
    assert engine.find_js_runtime() == ("deno", deno)
    deno.unlink()
    monkeypatch.setattr(engine.shutil, "which", lambda name: "C:/x/node.exe" if name == "node" else None)
    assert engine.find_js_runtime() == ("node", Path("C:/x/node.exe"))
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine.find_js_runtime() is None
