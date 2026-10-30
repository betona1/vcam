"""faster-whisper 래퍼: 설치·GPU 확인, 모델 탐색/로드, FFmpeg로 소리 추출, 전사.

소리는 vCAM이 이미 쓰는 외부 FFmpeg로 16kHz 모노 float32로 뽑아 numpy 배열로 넘긴다.
faster-whisper에 딸려 오는 PyAV를 실행 시점에 쓰지 않고, 진행률도 길이 기준으로 정확히 낸다.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from vcam.encoding.ffmpeg import CREATE_NO_WINDOW, FfmpegPaths
from vcam.subtitles.srt import Cue, Word, split_words
from vcam.util.paths import app_data_dir, redact_path

log = logging.getLogger(__name__)

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

SAMPLE_RATE = 16000

# 표시 이름 -> (HuggingFace 저장소, 설명, 대략 크기 GB)
MODELS: dict[str, tuple[str, str, float]] = {
    "large-v3": ("Systran/faster-whisper-large-v3", "최고 정확도", 3.0),
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "빠르고 정확", 1.6),
    "large-v2": ("Systran/faster-whisper-large-v2", "높은 정확도", 3.0),
    "medium": ("Systran/faster-whisper-medium", "중간", 1.5),
    "small": ("Systran/faster-whisper-small", "빠름", 0.5),
    "base": ("Systran/faster-whisper-base", "매우 빠름", 0.15),
}
# 자동 선택 순서: 이미 받아 둔 것 중 가장 정확한 모델
MODEL_PREFERENCE = ("large-v3", "large-v3-turbo", "large-v2", "medium", "small", "base")
FALLBACK_MODEL = "small"

CUDA_RUNTIME_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")
CUDA_ERROR_HINTS = ("cublas", "cudnn", "cuda", "cudart")


class SubtitleError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 한국어 메시지를 담는다."""


class Cancelled(SubtitleError):
    def __init__(self) -> None:
        super().__init__("취소되었습니다.")


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason: str = ""  # ok=False일 때 사용자 안내


def availability() -> Availability:
    """faster-whisper가 설치되어 있는지. 무거운 모듈을 실제로 import하지는 않는다."""
    missing = [name for name in ("faster_whisper", "ctranslate2") if importlib.util.find_spec(name) is None]
    if missing:
        return Availability(
            False,
            "자막 기능에 필요한 음성 인식 패키지가 설치되어 있지 않습니다.\n"
            "개발 환경: pip install \"vcam[subtitles]\"\n"
            "배포본: 자막 기능이 포함된 vCAM 빌드를 사용해 주세요.",
        )
    return Availability(True)


# ── GPU(CUDA) ─────────────────────────────────────────────────────────────────


def cuda_dll_dirs() -> list[Path]:
    """cuBLAS/cuDNN DLL을 찾을 폴더 후보. 환경 변수 → 앱 데이터 → 실행 파일 옆 → nvidia pip 패키지."""
    dirs: list[Path] = []
    env = os.environ.get("VCAM_CUDA_DIR")
    if env:
        dirs.append(Path(env))
    dirs.append(app_data_dir() / "cuda")
    dirs.append(Path(sys.executable).parent / "cuda")
    try:
        import nvidia  # type: ignore[import-not-found]  # noqa: PLC0415

        for base in nvidia.__path__:
            for sub in ("cublas", "cudnn"):
                dirs.append(Path(base) / sub / "bin")
    except ImportError:
        pass
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=1)
def register_cuda_dirs() -> tuple[str, ...]:
    """DLL 검색 경로에 CUDA 폴더를 한 번만 등록하고 등록한 폴더를 돌려준다."""
    if os.name != "nt":
        return ()
    added = []
    for d in cuda_dll_dirs():
        try:
            os.add_dll_directory(str(d))
        except OSError:
            continue
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        added.append(str(d))
    return tuple(added)


def cuda_runtime_present() -> bool:
    """GPU 드라이버가 있어도 cuBLAS/cuDNN DLL이 없으면 실행 중 실패하므로 미리 확인한다."""
    if os.name != "nt":
        return True
    register_cuda_dirs()
    for name in CUDA_RUNTIME_DLLS:
        try:
            ctypes.WinDLL(name)
        except OSError:
            return False
    return True


def cuda_available() -> bool:
    if not availability().ok:
        return False
    try:
        import ctranslate2  # noqa: PLC0415

        return ctranslate2.get_cuda_device_count() > 0 and cuda_runtime_present()
    except Exception:  # noqa: BLE001 - 드라이버 상태에 따라 어떤 예외든 날 수 있다
        log.info("CUDA 확인 실패", exc_info=True)
        return False


def is_cuda_error(error: BaseException) -> bool:
    """실행 중 예외가 CUDA 런타임 문제인지 (CPU 재시도 판단용)."""
    text = str(error).lower()
    return any(hint in text for hint in CUDA_ERROR_HINTS)


def resolve_device(choice: str = "auto") -> tuple[str, str]:
    """'auto' | 'cuda' | 'cpu' -> (device, compute_type)"""
    if choice == "cpu":
        return "cpu", "int8"
    if choice == "cuda" or (choice == "auto" and cuda_available()):
        return "cuda", "float16"
    return "cpu", "int8"


# ── 모델 ──────────────────────────────────────────────────────────────────────


def hf_cache_dir() -> Path:
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def is_model_cached(name: str) -> bool:
    repo = MODELS.get(name, (name, "", 0.0))[0]
    folder = hf_cache_dir() / ("models--" + repo.replace("/", "--")) / "snapshots"
    if not folder.is_dir():
        return False
    return any((snap / "model.bin").exists() for snap in folder.iterdir())


def cached_models() -> list[str]:
    return [name for name in MODEL_PREFERENCE if is_model_cached(name)]


def default_model() -> str:
    """받아 둔 모델 중 가장 정확한 것. 하나도 없으면 작은 모델."""
    cached = cached_models()
    return cached[0] if cached else FALLBACK_MODEL


def model_size_gb(name: str) -> float:
    return MODELS.get(name, ("", "", 0.0))[2]


def load_model(name: str, device: str = "auto", allow_download: bool = False, log_fn: Callable[[str], None] | None = None):
    """WhisperModel을 만든다. GPU 초기화가 실패하면 CPU로 한 번 더 시도한다. (model, device) 반환."""
    from faster_whisper import WhisperModel  # noqa: PLC0415

    repo = MODELS.get(name, (name, "", 0.0))[0]
    dev, compute = resolve_device(device)
    if dev == "cuda":
        register_cuda_dirs()
    cpu_threads = max(1, min(8, (os.cpu_count() or 4) - 2))
    cached = is_model_cached(name)
    if not cached and not allow_download:
        raise SubtitleError(
            f"음성 인식 모델 '{name}'이(가) PC에 없습니다 (약 {model_size_gb(name):.1f} GB).\n"
            "'모델이 없으면 내려받기'를 켜면 HuggingFace에서 받아 옵니다."
        )
    if log_fn:
        log_fn(f"모델 불러오는 중: {name} ({dev}, {compute})" + ("" if cached else " - 다운로드 중…"))
    attempts = [(dev, compute)]
    if dev == "cuda":
        attempts.append(("cpu", "int8"))
    last: Exception | None = None
    for d, c in attempts:
        try:
            model = WhisperModel(repo, device=d, compute_type=c, cpu_threads=cpu_threads, local_files_only=not allow_download)
        except Exception as error:  # noqa: BLE001 - 라이브러리가 던지는 예외 종류가 다양하다
            last = error
            log.warning("Whisper 모델 로드 실패 (%s/%s): %s", d, c, error)
            continue
        if log_fn and (d, c) != (dev, compute):
            log_fn("GPU 초기화에 실패해 CPU로 전환했습니다.")
        return model, d
    raise SubtitleError(f"음성 인식 모델을 불러오지 못했습니다 ({name}): {last}")


# ── 소리 추출과 전사 ──────────────────────────────────────────────────────────


def decode_audio(ffmpeg: FfmpegPaths, path: Path, cancel: Callable[[], bool] | None = None) -> np.ndarray:
    """FFmpeg로 16kHz 모노 float32 PCM을 뽑는다. 30분 영상이 약 115MB다."""
    args = [str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
            "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]  # fmt: skip
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    chunks: list[bytes] = []
    assert proc.stdout is not None
    try:
        while True:
            data = proc.stdout.read(1 << 20)
            if not data:
                break
            chunks.append(data)
            if cancel and cancel():
                proc.kill()
                raise Cancelled()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    if code != 0:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else f"종료 코드 {code}"
        raise SubtitleError(f"영상에서 소리를 읽지 못했습니다: {detail}")
    audio = np.frombuffer(b"".join(chunks), dtype=np.float32)
    if audio.size == 0:
        raise SubtitleError("영상에 소리 트랙이 없거나 비어 있습니다.")
    return audio


@dataclass(frozen=True)
class Transcript:
    cues: list[Cue]
    language: str
    language_probability: float
    duration_s: float


def transcribe(
    model,
    audio: np.ndarray,
    language: str | None = None,
    progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    log_fn: Callable[[str], None] | None = None,
    max_duration: float = 5.0,
    max_chars: int = 45,
) -> Transcript:
    """소리 배열을 전사해 자막 큐 목록을 만든다. 자막 본문은 로그에 남기지 않는다."""
    segments, info = model.transcribe(
        audio, language=language, task="transcribe", beam_size=5,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True, condition_on_previous_text=False,
        compression_ratio_threshold=2.4, no_speech_threshold=0.6,
    )  # fmt: skip
    duration = max(float(audio.size) / SAMPLE_RATE, 0.001)
    if log_fn:
        log_fn(f"감지 언어: {info.language} (확률 {info.language_probability:.0%}), 길이 {duration:.1f}초")
    cues: list[Cue] = []
    for segment in segments:
        if cancel and cancel():
            raise Cancelled()
        if not segment.text.strip() or segment.end <= segment.start:
            continue
        words = [Word(w.start, w.end, w.word) for w in (segment.words or [])]
        cues.extend(split_words(segment.start, segment.end, segment.text, words, max_duration, max_chars))
        if progress:
            progress(min(1.0, segment.end / duration))
    if progress:
        progress(1.0)
    return Transcript(cues, info.language, float(info.language_probability), duration)


def describe_source(path: Path) -> str:
    return redact_path(path)
