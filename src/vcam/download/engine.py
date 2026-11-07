"""yt-dlp 어댑터: 설치 확인, JS 런타임 탐색, 옵션 조립, 메타데이터 확인, 내려받기.

yt-dlp(Unlicense)는 선택 설치(`vcam[download]`)다. 이 모듈은 실제 내려받기 함수 안에서만
yt_dlp를 import하므로, 패키지가 없어도 앱과 단위 테스트는 동작한다. 모든 함수는 작업 스레드에서
부른다 — GUI 스레드에서 호출하지 않는다.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.util.paths import app_data_dir, cache_dir, redact_path

log = logging.getLogger(__name__)

KINDS = (("video", "영상 (MP4)"), ("audio", "소리만"))
QUALITIES = (
    ("best", "최고 화질"), ("2160", "4K (2160p)"), ("1440", "1440p"), ("1080", "1080p"),
    ("720", "720p"), ("480", "480p"), ("360", "360p"),
)  # fmt: skip
AUDIO_FORMATS = (("mp3", "MP3"), ("m4a", "M4A (AAC)"))
JS_RUNTIMES = ("deno", "node", "bun")  # yt-dlp가 지원하는 런타임 중 탐색 순서
DENO_INSTALL_HINT = "명령 프롬프트에서 'winget install DenoLand.Deno'를 실행한 뒤 vCAM을 다시 켜 주세요."
CURL_CFFI_INSTALL_HINT = "개발 환경에서는 pip install \"vcam[download]\" 로 curl_cffi를 설치하세요. 배포본에는 들어 있습니다."
# 로그인 쿠키를 가져올 곳. 값은 yt-dlp cookiesfrombrowser의 브라우저 이름이고 'file'은 Netscape 형식 cookies.txt.
COOKIE_SOURCES = (
    ("", "사용 안 함"), ("firefox", "Firefox"), ("chrome", "Chrome"), ("edge", "Edge"), ("brave", "Brave"),
    ("file", "cookies.txt 파일"),
)  # fmt: skip
COOKIE_PRIVACY_NOTE = (
    "로그인해야 보이는 영상(비공개·팔로워 전용·연령 확인)을 받을 때 이 PC의 브라우저에 저장된 로그인 쿠키를 읽어 "
    "그 영상 사이트에만 보냅니다. 다른 곳으로 보내거나 따로 저장하지 않습니다. "
    "Chrome·Edge 최신 버전은 쿠키 암호화 때문에 읽지 못할 수 있으니 그때는 Firefox 또는 cookies.txt 파일을 쓰세요."
)
# 로그인·연령 확인을 요구하는 오류 문구 (yt-dlp 추출기마다 표현이 다르다)
_LOGIN_RE = re.compile(
    r"sign in|log ?in|logged.in|age[- ]?(restricted|verification|gate)|confirm your age|not comfortable for some audiences"
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class DownloadError(Exception):
    """사용자에게 그대로 보여 줄 한국어 메시지를 담는다."""


class Cancelled(DownloadError):
    def __init__(self) -> None:
        super().__init__("취소되었습니다.")


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason: str = ""


def availability() -> Availability:
    """yt-dlp가 설치되어 있는지. 무거운 모듈을 실제로 import하지는 않는다."""
    if importlib.util.find_spec("yt_dlp") is None:
        return Availability(
            False,
            "인터넷 영상 내려받기에 필요한 yt-dlp 패키지가 설치되어 있지 않습니다.\n"
            "개발 환경: pip install \"vcam[download]\"\n"
            "배포본: 내려받기 기능이 포함된 vCAM 빌드를 사용해 주세요.",
        )
    return Availability(True)


def engine_version() -> str:
    try:
        from yt_dlp.version import __version__
    except ImportError:
        return "없음"
    return __version__


def impersonation_available() -> Availability:
    """TikTok·Instagram 등이 요구하는 브라우저 위장(TLS 지문). yt-dlp가 curl_cffi 패키지로 제공한다.

    없어도 YouTube 등 대부분의 사이트는 되지만, 위장을 요구하는 사이트는 '자동 접속 차단'으로 실패한다.
    """
    try:
        import yt_dlp.networking._curlcffi  # noqa: F401 - curl_cffi가 없거나 지원하지 않는 버전이면 ImportError
    except ImportError as exc:
        return Availability(False, f"브라우저 위장 모듈(curl_cffi)을 쓸 수 없습니다: {exc}")
    return Availability(True)


def find_js_runtime() -> tuple[str, Path] | None:
    """YouTube 서명 해독에 쓰는 JS 런타임. PATH와 %LOCALAPPDATA%\\vcam\\deno 를 찾는다.

    없어도 내려받기는 되지만 yt-dlp가 일부 화질을 못 받을 수 있다고 경고한다.
    """
    bundled = app_data_dir() / "deno" / "deno.exe"
    if bundled.is_file():
        return "deno", bundled
    for name in JS_RUNTIMES:
        found = shutil.which(name)
        if found:
            return name, Path(found)
    return None


def extract_urls(text: str) -> list[str]:
    """붙여 넣은 텍스트에서 http(s) 주소만 순서대로, 중복 없이 뽑는다."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.findall(text or ""):
        url = match.rstrip(".,;)")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def is_probable_url(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and "\n" not in text and _URL_RE.fullmatch(text) is not None


# ── 옵션 ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DownloadOptions:
    kind: str = "video"  # video | audio
    quality: str = "best"  # best | 2160 | 1440 | 1080 | 720 | 480 | 360
    audio_format: str = "mp3"  # mp3 | m4a
    subtitles: bool = False  # 사이트가 제공하는 자막(SRT) 함께 받기
    auto_subtitles: bool = False  # 자동 생성 자막도 받기
    subtitle_langs: tuple[str, ...] = ("ko", "en")
    playlist: bool = False  # 재생목록 주소면 전체 받기
    cookies_browser: str = ""  # COOKIE_SOURCES의 값. 비우면 로그인 없이 받는다
    cookies_file: str = ""  # cookies_browser == 'file' 일 때 cookies.txt 경로
    output_dir: str = ""  # 작업 폴더

    @property
    def quality_label(self) -> str:
        return dict(QUALITIES).get(self.quality, self.quality)


def format_selector(options: DownloadOptions) -> str:
    """yt-dlp -f 식. 영상은 '화질 이하의 최고 영상 + 최고 소리', 소리만이면 최고 소리."""
    if options.kind == "audio":
        return "ba/b"
    if options.quality == "best" or not options.quality.isdigit():
        return "bv*+ba/b"
    h = int(options.quality)
    return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"


def cookie_options(options: DownloadOptions) -> dict:
    """로그인 쿠키 설정 → yt-dlp 옵션. 정보 읽기와 내려받기 양쪽에 똑같이 넣는다."""
    if options.cookies_browser == "file":
        return {"cookiefile": options.cookies_file} if options.cookies_file else {}
    if options.cookies_browser:
        return {"cookiesfrombrowser": (options.cookies_browser,)}
    return {}


def output_template(output_dir: Path) -> str:
    # 재생목록이면 '001 - 제목', 아니면 '제목'. 윈도우에서 못 쓰는 문자는 yt-dlp가 바꾼다.
    return str(output_dir / "%(playlist_index&{} - |)s%(title).150B.%(ext)s")


def build_ydl_options(
    options: DownloadOptions,
    ffmpeg: FfmpegPaths | None,
    js_runtime: tuple[str, Path] | None = None,
) -> dict:
    """yt-dlp YoutubeDL에 넘길 옵션. 후크·로거는 download()가 덧붙인다. (순수 함수, 테스트 대상)"""
    out_dir = Path(options.output_dir) if options.output_dir else Path.cwd()
    opts: dict = {
        "quiet": True,
        "noprogress": True,
        "no_color": True,
        "format": format_selector(options),
        # 추출기가 매긴 우선순위(ie_pref: TikTok 워터마크 포맷 등을 뒤로) → 해상도 → FPS →
        # 같은 화질이면 H.264/AAC(MP4 호환)를 우선한다
        "format_sort": ["ie_pref", "res", "fps", "vcodec:h264", "acodec:m4a"],
        "outtmpl": {"default": output_template(out_dir)},
        "windowsfilenames": True,
        "overwrites": False,  # 사용자 파일을 덮어쓰지 않는다 (같은 이름이 있으면 이미 받은 것으로 본다)
        "noplaylist": not options.playlist,
        "ignoreerrors": "only_download" if options.playlist else False,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "cachedir": str(cache_dir() / "yt-dlp"),
        "postprocessors": [],
        **cookie_options(options),
    }
    if ffmpeg is not None:
        opts["ffmpeg_location"] = str(ffmpeg.ffmpeg.parent)
    if js_runtime is not None:
        name, path = js_runtime
        opts["js_runtimes"] = {name: {"path": str(path)}}
    if options.kind == "audio":
        opts["postprocessors"].append(
            {"key": "FFmpegExtractAudio", "preferredcodec": options.audio_format, "preferredquality": "192"}
        )
    else:
        opts["merge_output_format"] = "mp4"
        # 영상만 있는 포맷에 소리를 합칠 때 결과도 MP4가 되게 한다
        opts["postprocessors"].append({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"})
    if options.subtitles or options.auto_subtitles:
        opts.update(
            writesubtitles=options.subtitles,
            writeautomaticsub=options.auto_subtitles,
            subtitleslangs=list(options.subtitle_langs) or ["ko", "en"],
            subtitlesformat="srt/best",
        )
        opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
    return opts


# ── 메타데이터 확인 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    title: str
    duration_s: float = 0.0
    uploader: str = ""
    thumbnail_url: str = ""
    is_playlist: bool = False
    entry_count: int = 0
    thumbnail: bytes = b""


def _probe_options(options: DownloadOptions, js_runtime: tuple[str, Path] | None) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "no_color": True,
        "skip_download": True, "noplaylist": not options.playlist,
        "extract_flat": "in_playlist", "cachedir": str(cache_dir() / "yt-dlp"),
        **cookie_options(options),
    }  # fmt: skip
    if js_runtime is not None:
        opts["js_runtimes"] = {js_runtime[0]: {"path": str(js_runtime[1])}}
    return opts


def fetch_thumbnail(url: str, timeout: float = 8.0, limit: int = 2 * 1024 * 1024) -> bytes:
    """목록에 보여 줄 작은 썸네일. 실패하면 빈 바이트 (내려받기에는 영향 없음)."""
    if not url:
        return b""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 사용자가 넣은 영상 주소의 썸네일
            return resp.read(limit)
    except (OSError, ValueError) as exc:
        log.debug("썸네일을 받지 못함: %s", exc)
        return b""


def probe(url: str, options: DownloadOptions, js_runtime: tuple[str, Path] | None = None) -> MediaCandidate:
    """제목·길이·썸네일을 미리 읽는다. 재생목록이면 항목 수만 센다 (각 영상은 내려받을 때 읽음)."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError(availability().reason) from exc
    try:
        with yt_dlp.YoutubeDL(_probe_options(options, js_runtime)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_friendly_error(str(exc))) from exc
    if info is None:
        raise DownloadError("주소에서 영상 정보를 읽지 못했습니다.")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        first_thumb = next((e.get("thumbnail") or "" for e in entries if e.get("thumbnail")), "")
        thumb_url = info.get("thumbnail") or (info.get("thumbnails") or [{}])[-1].get("url", "") or first_thumb
        return MediaCandidate(
            url=url, title=info.get("title") or "재생목록", uploader=info.get("uploader") or info.get("channel") or "",
            thumbnail_url=thumb_url, is_playlist=True, entry_count=len(entries), thumbnail=fetch_thumbnail(thumb_url),
        )  # fmt: skip
    thumb_url = info.get("thumbnail") or ""
    return MediaCandidate(
        url=url, title=info.get("title") or url, duration_s=float(info.get("duration") or 0.0),
        uploader=info.get("uploader") or info.get("channel") or "", thumbnail_url=thumb_url,
        thumbnail=fetch_thumbnail(thumb_url),
    )  # fmt: skip


def _friendly_error(message: str) -> str:
    text = message.replace("ERROR: ", "").strip()
    lowered = text.lower()
    if "unsupported url" in lowered:
        return "지원하지 않는 주소입니다. 영상 페이지 주소를 넣어 주세요."
    if "private video" in lowered:
        return "비공개 영상이라 내려받을 수 없습니다."
    if "ip address is blocked" in lowered or "not available in your country" in lowered or "geo-restricted" in lowered:
        return "이 영상은 지금 접속한 지역(IP)에서 볼 수 없게 막혀 있습니다."
    if _LOGIN_RE.search(lowered):
        return "로그인이나 연령 확인이 필요한 영상입니다. 옵션의 '로그인 쿠키'에서 로그인해 둔 브라우저를 고른 뒤 다시 받아 보세요."
    if "impersonat" in lowered or "unexpected response from webpage" in lowered or "unable to extract challenge" in lowered:
        if impersonation_available().ok:
            return "사이트가 자동 접속을 막았습니다. 잠시 뒤 다시 받아 보세요. (" + text[:120] + ")"
        return "사이트가 브라우저 위장을 요구하는데 curl_cffi 모듈이 없습니다. " + CURL_CFFI_INSTALL_HINT
    if "no video formats found" in lowered or "no video could be found" in lowered or "no video in this post" in lowered:
        return "이 게시물에는 받을 영상이 없습니다 (사진 게시물 등)."
    if "too many requests" in lowered or "rate limit" in lowered or "rate-limit" in lowered or "http error 429" in lowered:
        return "사이트가 요청을 제한했습니다. 잠시 뒤 다시 받아 보세요."
    if "video unavailable" in lowered or "is unavailable" in lowered:
        return "영상을 찾을 수 없거나 더 이상 볼 수 없습니다."
    if "requested format is not available" in lowered:
        return "선택한 화질·형식을 이 영상은 제공하지 않습니다. 다른 화질을 골라 주세요."
    if "ffmpeg" in lowered or "ffprobe" in lowered:
        return "FFmpeg가 필요합니다. 설정에서 FFmpeg 위치를 지정하거나 설치해 주세요."
    if "urlopen error" in lowered or "network" in lowered or "timed out" in lowered or "connection" in lowered:
        return "인터넷 연결을 확인해 주세요. (" + text[:120] + ")"
    if "unable to download" in lowered or "http error" in lowered:
        return "서버가 응답하지 않거나 거부했습니다. (" + text[:160] + ")"
    return text[:300] or "알 수 없는 오류"


# ── 내려받기 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Progress:
    stage: str  # probe | download | postprocess | done
    fraction: float = 0.0  # 0~1 (전체를 알 수 없으면 마지막 값 유지)
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0  # bytes/s
    eta_s: int = -1
    filename: str = ""
    index: int = 0  # 재생목록 안에서 몇 번째 항목인지 (1부터), 단일 영상은 0
    count: int = 0  # 재생목록 항목 수


@dataclass
class DownloadResult:
    files: list[Path] = field(default_factory=list)  # 최종 영상/소리 파일
    subtitles: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # 재생목록 일부 실패 등
    duration_s: float = 0.0  # 단일 영상의 길이. 사이트가 안 주면(Instagram 등) 받은 파일에서 읽는다


STAGE_LABELS = {"probe": "정보 읽는 중", "download": "내려받는 중", "postprocess": "변환·합치는 중", "done": "완료"}


class _Logger:
    """yt-dlp 메시지를 vCAM 로그와 대화상자 기록으로 보낸다."""

    def __init__(self, log_fn: Callable[[str], None] | None) -> None:
        self._log_fn = log_fn

    def _emit(self, level: int, message: str) -> None:
        text = message.strip()
        if not text or text.startswith("[debug]"):
            return
        log.log(level, "yt-dlp: %s", redact_path(text))
        # 진행 줄과 yt-dlp 내부 안내(impersonation 등)는 파일 로그에만 남기고 창에는 보여 주지 않는다
        if self._log_fn and not text.startswith("[download]") and "impersonat" not in text.lower():
            self._log_fn(text)

    def debug(self, message: str) -> None:
        self._emit(logging.DEBUG, message)

    def info(self, message: str) -> None:
        self._emit(logging.INFO, message)

    def warning(self, message: str) -> None:
        self._emit(logging.WARNING, "경고: " + message.replace("WARNING: ", ""))

    def error(self, message: str) -> None:
        self._emit(logging.ERROR, "오류: " + message.replace("ERROR: ", ""))


def download(
    url: str,
    options: DownloadOptions,
    ffmpeg: FfmpegPaths | None,
    *,
    progress: Callable[[Progress], None] | None = None,
    log_fn: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] = lambda: False,
    js_runtime: tuple[str, Path] | None = None,
) -> DownloadResult:
    """주소 하나(또는 재생목록)를 내려받아 최종 파일 경로를 돌려준다. 취소하면 Cancelled."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError(availability().reason) from exc
    if options.kind == "video" and ffmpeg is None:
        log.warning("FFmpeg 없이 내려받기: 영상과 소리를 합칠 수 없어 낮은 화질로 받을 수 있습니다")
    result = DownloadResult()
    state = {"index": 0, "count": 0, "last_fraction": 0.0}

    def check_cancel() -> None:
        if cancel():
            raise yt_dlp.utils.DownloadCancelled()

    def report(p: Progress) -> None:
        if progress:
            progress(p)

    def progress_hook(d: dict) -> None:
        check_cancel()
        status = d.get("status")
        info = d.get("info_dict") or {}
        if info.get("playlist_index"):
            state["index"] = int(info.get("playlist_index") or 0)
            state["count"] = int(info.get("n_entries") or info.get("playlist_count") or state["count"])
        name = Path(d.get("filename") or "").name
        if status == "downloading":
            done = int(d.get("downloaded_bytes") or 0)
            total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            frac = (done / total) if total else state["last_fraction"]
            state["last_fraction"] = frac
            eta = d.get("eta")
            report(Progress("download", frac, done, total, float(d.get("speed") or 0.0),
                            int(eta) if eta is not None else -1, name, state["index"], state["count"]))  # fmt: skip
        elif status == "finished":
            state["last_fraction"] = 0.0
            report(Progress("postprocess", 1.0, filename=name, index=state["index"], count=state["count"]))
        elif status == "error":
            report(Progress("download", state["last_fraction"], filename=name, index=state["index"], count=state["count"]))

    def pp_hook(d: dict) -> None:
        check_cancel()
        if d.get("status") == "started":
            report(Progress("postprocess", 1.0, filename=str(d.get("postprocessor") or ""),
                            index=state["index"], count=state["count"]))  # fmt: skip

    def post_hook(filename: str) -> None:
        path = Path(filename)
        if path.suffix.lower() == ".srt":
            result.subtitles.append(path)
        else:
            result.files.append(path)

    opts = build_ydl_options(options, ffmpeg, js_runtime)
    opts.update(
        logger=_Logger(log_fn),
        progress_hooks=[progress_hook],
        postprocessor_hooks=[pp_hook],
        post_hooks=[post_hook],
    )
    report(Progress("probe"))
    check_cancel()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadCancelled as exc:
        raise Cancelled() from exc
    except yt_dlp.utils.DownloadError as exc:
        if cancel():
            raise Cancelled() from exc
        raise DownloadError(_friendly_error(str(exc))) from exc
    except Exception as exc:  # noqa: BLE001 - yt-dlp 내부 오류도 사용자에게 한 줄로
        if cancel():
            raise Cancelled() from exc
        log.error("yt-dlp 내부 오류", exc_info=True)
        raise DownloadError(f"내려받기 중 오류: {exc}") from exc
    _collect_from_info(info, result)
    if not result.files:
        raise DownloadError("내려받은 파일이 없습니다. 주소와 화질·형식을 확인해 주세요.")
    # 같은 파일이 두 번 들어가지 않게 하고(자막 변환 후 MoveFiles 등), 실제 있는 파일만 남긴다
    result.files = _dedupe([p for p in result.files if p.exists()])
    result.subtitles = _dedupe([p for p in result.subtitles if p.exists()])
    result.duration_s = _duration_of(info, result.files, ffmpeg)
    report(Progress("done", 1.0, index=state["index"], count=state["count"]))
    return result


def _duration_of(info: dict | None, files: list[Path], ffmpeg: FfmpegPaths | None) -> float:
    """단일 영상의 길이(초). 추출기가 안 주면 받은 파일을 ffprobe로 읽는다 (재생목록은 0)."""
    if not info or info.get("_type") == "playlist":
        return 0.0
    if info.get("duration"):
        return float(info["duration"])
    if ffmpeg is None or not files:
        return 0.0
    from vcam.encoding.validator import ValidationError, probe_media

    try:
        return probe_media(ffmpeg, files[0]).duration_s
    except (ValidationError, OSError, ValueError) as exc:  # 소리만 받은 파일 등
        log.debug("길이를 읽지 못함: %s", exc)
        return 0.0


def _collect_from_info(info: dict | None, result: DownloadResult) -> None:
    """post_hooks가 안 불린 경우(이미 받은 파일 등)에 대비해 info_dict에서도 최종 경로를 모은다."""
    if not info:
        return
    entries = [e for e in (info.get("entries") or [])] if info.get("_type") == "playlist" else [info]
    for entry in entries:
        if not entry:
            result.errors.append("재생목록의 한 항목을 받지 못했습니다.")
            continue
        for rd in entry.get("requested_downloads") or []:
            path = rd.get("filepath") or rd.get("filename")
            if path:
                result.files.append(Path(path))
        for sub in (entry.get("requested_subtitles") or {}).values():
            path = (sub or {}).get("filepath")
            if path:
                result.subtitles.append(Path(path))


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
