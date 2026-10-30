"""영상 하나를 끝까지 처리: 소리 추출 → 전사 → 원문 SRT → (선택) 번역 SRT.

GUI 스레드에서 호출하지 않는다. 진행률·로그는 콜백으로 알리고 자막 본문은 로그에 남기지 않는다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.subtitles import languages, translator
from vcam.subtitles import transcriber as tr
from vcam.subtitles.srt import Cue, subtitle_path, write_srt
from vcam.util.paths import redact_path

log = logging.getLogger(__name__)

Progress = Callable[[str, float], None]  # (stage, 0~1)  stage: "decode" | "transcribe" | "translate"
LogFn = Callable[[str], None]

STAGE_LABELS = {"decode": "소리 읽는 중", "load": "모델 준비 중", "transcribe": "음성 인식 중", "translate": "번역 중"}


@dataclass(frozen=True)
class SubtitleOptions:
    model: str = ""  # 빈 문자열 = 받아 둔 모델 중 자동 선택
    device: str = "auto"  # auto | cuda | cpu
    source_language: str = languages.AUTO
    allow_download: bool = False
    translate: bool = False
    targets: tuple[str, ...] = ("ko",)
    allow_online: bool = False  # 온라인 번역(Google/MyMemory) 허용 — 자막 본문이 외부로 전송된다
    model_dir: str = ""  # 오프라인 번역 모델 폴더(또는 상위 폴더)
    max_chars: int = 45
    max_duration: float = 5.0
    output_dir: str = ""  # 빈 문자열 = 영상과 같은 폴더

    @property
    def resolved_model(self) -> str:
        return self.model or tr.default_model()


@dataclass
class SubtitleResult:
    source: Path
    original: Path | None = None
    detected: str = ""
    cue_count: int = 0
    duration_s: float = 0.0
    translated: dict[str, Path] = field(default_factory=dict)  # target -> path
    errors: list[str] = field(default_factory=list)  # 번역 일부 실패 등 부분 성공 사유

    @property
    def files(self) -> list[Path]:
        return [p for p in (self.original, *self.translated.values()) if p]


class EngineCache:
    """같은 모델·장치면 다시 불러오지 않도록 Whisper 모델과 번역 체인을 보관한다 (앱 수명 동안)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model_key: tuple[str, str] | None = None
        self._model = None
        self.device = ""
        self._chain_key: tuple | None = None
        self._chain: list[translator.Engine] | None = None

    def get_model(self, name: str, device: str, allow_download: bool, log_fn: LogFn | None):
        with self._lock:
            key = (name, device)
            if self._model is None or self._model_key != key:
                self._model, self.device = tr.load_model(name, device, allow_download, log_fn)
                self._model_key = key
            return self._model

    def get_chain(self, device: str, model_dir: str, allow_online: bool, log_fn: LogFn | None) -> list[translator.Engine]:
        with self._lock:
            key = (device, model_dir, allow_online)
            if self._chain is None or self._chain_key != key:
                self._chain = translator.build_chain(device, model_dir or None, allow_online, log_fn)
                self._chain_key = key
            return self._chain

    def clear(self) -> None:
        with self._lock:
            self._model = None
            self._model_key = None
            self._chain = None
            self._chain_key = None


def process_file(
    path: Path,
    options: SubtitleOptions,
    ffmpeg: FfmpegPaths,
    model,
    chain: list[translator.Engine] | None = None,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
    log_fn: LogFn | None = None,
) -> SubtitleResult:
    source = Path(path)
    result = SubtitleResult(source=source)
    if not source.is_file():
        raise tr.SubtitleError(f"파일을 찾을 수 없습니다: {source.name}")

    def report(stage: str, fraction: float) -> None:
        if progress:
            progress(stage, fraction)

    report("decode", 0.0)
    audio = tr.decode_audio(ffmpeg, source, cancel)
    report("decode", 1.0)

    language = None
    if options.source_language != languages.AUTO:
        lang = languages.get(options.source_language)
        language = lang.whisper if lang else options.source_language
    transcript = tr.transcribe(
        model, audio, language=language,
        progress=lambda f: report("transcribe", f), cancel=cancel, log_fn=log_fn,
        max_duration=options.max_duration, max_chars=options.max_chars,
    )  # fmt: skip
    del audio
    if not transcript.cues:
        raise tr.SubtitleError("인식된 대사가 없습니다. 소리가 녹음되었는지, 음성 언어 설정이 맞는지 확인해 주세요.")
    result.detected = transcript.language
    result.cue_count = len(transcript.cues)
    result.duration_s = transcript.duration_s

    out_dir = Path(options.output_dir) if options.output_dir else None
    detected_lang = languages.get(transcript.language)
    label = detected_lang.code if detected_lang else transcript.language
    result.original = subtitle_path(source, label, out_dir)
    write_srt(result.original, transcript.cues)
    log.info("원문 자막 저장: %s (%d개 큐, 언어 %s)", redact_path(result.original), len(transcript.cues), label)
    if log_fn:
        log_fn(f"원문 자막 저장: {result.original.name} ({len(transcript.cues)}개)")

    if options.translate and options.targets:
        _translate(result, transcript.cues, label, options, chain or [], report, cancel, log_fn, out_dir)
    return result


def _translate(result: SubtitleResult, cues: list[Cue], label: str, options: SubtitleOptions,
               chain: list[translator.Engine], report: Progress, cancel, log_fn: LogFn | None,
               out_dir: Path | None) -> None:  # fmt: skip
    texts = [c.text for c in cues]
    targets: list[str] = []
    skipped: list[str] = []
    for t in options.targets:
        lang = languages.get(t)
        if lang and lang.whisper == result.detected:
            skipped.append(t)
        else:
            targets.append(t)
    if skipped and log_fn:
        log_fn("원문과 같은 언어라 번역 생략: " + ", ".join(languages.display_name(t) for t in skipped))
    if not targets:
        return
    if not chain:
        result.errors.append("번역 엔진이 준비되지 않아 번역 자막을 만들지 못했습니다.")
        return
    for index, target in enumerate(targets):
        if cancel and cancel():
            raise tr.Cancelled()
        try:
            translated = translator.translate_cues(chain, texts, label, target, log_fn=log_fn)
            out = subtitle_path(result.source, target, out_dir)
            write_srt(out, [Cue(c.start, c.end, t) for c, t in zip(cues, translated, strict=True)])
        except Exception as error:  # noqa: BLE001 - 한 언어 실패가 다른 언어를 막지 않게 한다
            message = f"{languages.display_name(target)} 번역 실패: {error}"
            result.errors.append(message)
            log.warning("번역 실패 (%s): %s", target, error)
            if log_fn:
                log_fn(message)
        else:
            result.translated[target] = out
            if log_fn:
                log_fn(f"번역 자막 저장: {out.name}")
        report("translate", (index + 1) / len(targets))


def run_job(
    paths: list[Path],
    options: SubtitleOptions,
    ffmpeg: FfmpegPaths,
    cache: EngineCache,
    on_file_start: Callable[[int, Path], None] | None = None,
    on_file_done: Callable[[int, SubtitleResult], None] | None = None,
    on_file_failed: Callable[[int, str], None] | None = None,
    progress: Callable[[int, str, float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    log_fn: LogFn | None = None,
) -> tuple[int, int]:
    """여러 파일을 차례로 처리한다. (성공 수, 실패 수) 반환. 작업 스레드에서 호출한다."""
    success = failure = 0
    model_name = options.resolved_model
    device = options.device
    try:
        if progress:
            progress(-1, "load", 0.0)
        model = cache.get_model(model_name, device, options.allow_download, log_fn)
        if log_fn:
            log_fn(f"모델 준비 완료: {model_name} ({cache.device})")
        chain: list[translator.Engine] | None = None
        if options.translate and options.targets:
            try:
                chain = cache.get_chain(device, options.model_dir, options.allow_online, log_fn)
                if log_fn:
                    log_fn("번역 엔진: " + " → ".join(e.name for e in chain))
            except translator.TranslationError as error:
                chain = []
                if log_fn:
                    log_fn(str(error))
    except tr.SubtitleError as error:
        for index, _ in enumerate(paths):
            if on_file_failed:
                on_file_failed(index, str(error))
        return 0, len(paths)
    except Exception as error:  # noqa: BLE001 - 모델 로드 실패는 전부 사용자에게 보여 준다
        log.error("자막 모델 준비 실패", exc_info=True)
        for index, _ in enumerate(paths):
            if on_file_failed:
                on_file_failed(index, f"모델 준비 실패: {error}")
        return 0, len(paths)

    for index, path in enumerate(paths):
        if cancel and cancel():
            failure += 1
            if on_file_failed:
                on_file_failed(index, "취소됨")
            continue
        if on_file_start:
            on_file_start(index, path)

        def file_progress(stage: str, fraction: float, i: int = index) -> None:
            if progress:
                progress(i, stage, fraction)

        try:
            try:
                result = process_file(path, options, ffmpeg, model, chain, file_progress, cancel, log_fn)
            except tr.SubtitleError:
                raise
            except Exception as error:
                if device == "cpu" or not tr.is_cuda_error(error):
                    raise
                # GPU 런타임(cuBLAS/cuDNN) 문제 → CPU로 다시 불러와 한 번만 더 시도
                if log_fn:
                    log_fn(f"GPU 실행 실패 ({error}). CPU로 전환해 다시 시도합니다.")
                device = "cpu"
                model = cache.get_model(model_name, "cpu", options.allow_download, log_fn)
                if chain:
                    chain = cache.get_chain("cpu", options.model_dir, options.allow_online, log_fn)
                result = process_file(path, options, ffmpeg, model, chain, file_progress, cancel, log_fn)
        except tr.Cancelled:
            failure += 1
            if on_file_failed:
                on_file_failed(index, "취소됨")
        except tr.SubtitleError as error:
            failure += 1
            if on_file_failed:
                on_file_failed(index, str(error))
        except Exception as error:  # noqa: BLE001 - 한 파일의 실패가 나머지를 막지 않게 한다
            log.error("자막 생성 실패: %s", redact_path(path), exc_info=True)
            failure += 1
            if on_file_failed:
                on_file_failed(index, f"알 수 없는 오류: {error}")
        else:
            success += 1
            if on_file_done:
                on_file_done(index, result)
    return success, failure
