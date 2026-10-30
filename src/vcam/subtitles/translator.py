"""번역 엔진: 오프라인 CTranslate2 모델(M2M100 기본) 우선, 사용자가 허용하면 온라인 Google/MyMemory 보조.

온라인 엔진은 자막 본문을 외부 서비스로 보내므로 기본값은 끄고, 사용자가 명시적으로 켠 경우에만 체인에 넣는다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from vcam.subtitles import languages
from vcam.subtitles.srt import SENTENCE_END
from vcam.util.paths import app_data_dir

log = logging.getLogger(__name__)

# 오프라인 모델 폴더 이름 -> 종류. 앞에 있는 것을 우선한다.
#  - m2m100_418M : MIT (상업 이용 가능) -> 기본 모델
#  - nllb-200     : CC-BY-NC-4.0 (비상업 전용) -> 배포하지 않으며, 사용자가 직접 넣은 경우에만 인식
OFFLINE_MODELS: tuple[tuple[str, str], ...] = (
    ("m2m100_418M-ct2-int8", "m2m100"),
    ("m2m100_1.2B-ct2-int8", "m2m100"),
    ("nllb-200-distilled-600M-ct2-int8", "nllb"),
)

LogFn = Callable[[str], None] | None


class TranslationError(Exception):
    pass


class Engine(Protocol):
    name: str

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]: ...


def model_roots(extra: str | Path | None = None) -> list[Path]:
    """오프라인 번역 모델을 찾을 폴더 후보."""
    roots: list[Path] = []
    if extra:
        roots.append(Path(extra))
    env = os.environ.get("VCAM_MODEL_DIR")
    if env:
        roots.append(Path(env))
    roots.append(app_data_dir() / "models")
    roots.append(Path(sys.executable).parent / "models")
    if not getattr(sys, "frozen", False):
        roots.append(Path(__file__).resolve().parents[3] / "models")
    return roots


def _is_model_dir(folder: Path) -> bool:
    return (folder / "model.bin").exists() and (folder / "sentencepiece.bpe.model").exists()


def _model_kind(folder: Path) -> str:
    name = folder.name.lower()
    for dir_name, kind in OFFLINE_MODELS:
        if dir_name.lower() in name:
            return kind
    try:
        cfg = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        if any(str(v).startswith("__") for v in (cfg.get("bos_token"), cfg.get("decoder_start_token"))):
            return "m2m100"
    except (OSError, ValueError):
        pass
    return "nllb" if "nllb" in name else "m2m100"


def find_offline_model(extra: str | Path | None = None) -> tuple[Path, str] | None:
    """사용 가능한 오프라인 번역 모델 폴더. (Path, kind) 또는 None.

    extra는 모델 폴더 자체이거나 모델 폴더들을 담은 상위 폴더일 수 있다.
    """
    candidates: list[Path] = []
    for root in model_roots(extra):
        candidates.append(root)
        candidates.extend(root / dir_name for dir_name, _kind in OFFLINE_MODELS)
    for folder in candidates:
        if _is_model_dir(folder):
            return folder, _model_kind(folder)
    return None


class CT2Translator:
    """CTranslate2로 변환한 M2M100 / NLLB-200 모델로 오프라인 번역한다."""

    def __init__(self, model_dir: Path, device: str = "auto", kind: str | None = None) -> None:
        import ctranslate2  # noqa: PLC0415
        import sentencepiece as spm  # noqa: PLC0415

        from vcam.subtitles.transcriber import cuda_available, register_cuda_dirs  # noqa: PLC0415

        self.kind = kind or _model_kind(model_dir)
        self.name = "M2M100 (오프라인)" if self.kind == "m2m100" else "NLLB (오프라인)"
        # 한글 등 비ASCII 경로에서 sentencepiece의 파일 열기가 실패하므로 바이트로 읽어 넘긴다.
        self.sp = spm.SentencePieceProcessor(model_proto=(Path(model_dir) / "sentencepiece.bpe.model").read_bytes())
        use_cuda = device != "cpu" and cuda_available()
        if use_cuda:
            register_cuda_dirs()
        try:
            self.translator = ctranslate2.Translator(
                str(model_dir), device="cuda" if use_cuda else "cpu", compute_type="int8_float16" if use_cuda else "int8"
            )
        except Exception:  # noqa: BLE001 - GPU 실패는 CPU로 대체
            log.warning("번역 모델 GPU 초기화 실패, CPU로 전환", exc_info=True)
            self.translator = ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8")

    def _lang_token(self, lang: languages.Language) -> str:
        return f"__{lang.m2m}__" if self.kind == "m2m100" else _NLLB_CODES.get(lang.code, lang.code)

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        src, tgt = languages.get(source), languages.get(target)
        if not src or not tgt:
            raise TranslationError(f"{self.name}가 지원하지 않는 언어: {source} -> {target}")
        src_token, tgt_token = self._lang_token(src), self._lang_token(tgt)
        out: list[str] = []
        batch = 16
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            tokens = [[src_token, *self.sp.encode(t, out_type=str), "</s>"] for t in chunk]
            results = self.translator.translate_batch(
                tokens, target_prefix=[[tgt_token]] * len(chunk), beam_size=4, max_decoding_length=256, repetition_penalty=1.1
            )
            for r in results:
                hyp = r.hypotheses[0]
                if hyp and hyp[0] == tgt_token:
                    hyp = hyp[1:]
                out.append(self.sp.decode(hyp).strip())
        return out


# NLLB-200 언어 코드 (사용자가 직접 넣은 비상업용 모델에만 쓰인다)
_NLLB_CODES = {
    "ko": "kor_Hang", "en": "eng_Latn", "ja": "jpn_Jpan", "zh": "zho_Hans", "zh-TW": "zho_Hant",
    "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn", "pt": "por_Latn", "it": "ita_Latn",
    "ru": "rus_Cyrl", "vi": "vie_Latn", "th": "tha_Thai", "id": "ind_Latn", "ar": "arb_Arab",
    "hi": "hin_Deva", "tr": "tur_Latn",
}  # fmt: skip


def _retry(func: Callable[[], str], attempts: int = 3, delay: float = 1.5) -> str:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return func()
        except Exception as error:  # noqa: BLE001 - 네트워크 라이브러리 예외 종류가 다양하다
            last = error
            time.sleep(delay * (i + 1))
    raise TranslationError(str(last))


def _translate_lines(client, texts: Sequence[str], limit: int) -> list[str]:
    """여러 줄을 묶어 요청 횟수를 줄이고, 줄 수가 어긋나면 한 줄씩 다시 번역한다."""
    out: list[str] = []
    group: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal group, size
        if not group:
            return
        joined = "\n".join(group)
        result = _retry(lambda: client.translate(joined))
        lines = (result or "").split("\n")
        if len(lines) != len(group):
            lines = [_retry(lambda t=t: client.translate(t)) if t.strip() else t for t in group]
        out.extend(line.strip() for line in lines)
        group, size = [], 0

    for text in texts:
        if size + len(text) + 1 > limit:
            flush()
        group.append(text)
        size += len(text) + 1
    flush()
    return out


class GoogleTranslator:
    name = "Google (온라인)"

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        from deep_translator import GoogleTranslator as DT  # noqa: PLC0415

        src, tgt = languages.get(source), languages.get(target)
        client = DT(source=src.google if src else "auto", target=tgt.google if tgt else target)
        return _translate_lines(client, texts, limit=4500)


class MyMemoryTranslator:
    name = "MyMemory (온라인)"

    def translate(self, texts: Sequence[str], source: str, target: str) -> list[str]:
        from deep_translator import MyMemoryTranslator as DT  # noqa: PLC0415

        src, tgt = languages.get(source), languages.get(target)
        if not src or not tgt:
            raise TranslationError(f"MyMemory가 지원하지 않는 언어: {source} -> {target}")
        client = DT(source=src.mymemory, target=tgt.mymemory)
        return [_retry(lambda t=t: client.translate(t[:500])) if t.strip() else t for t in texts]


def build_chain(device: str = "auto", model_dir: str | Path | None = None, allow_online: bool = False,
                log_fn: LogFn = None) -> list[Engine]:  # fmt: skip
    """시도할 번역기 목록. 오프라인 모델이 없고 온라인도 허용하지 않으면 TranslationError."""
    chain: list[Engine] = []
    found = find_offline_model(model_dir)
    if found:
        folder, kind = found
        try:
            chain.append(CT2Translator(folder, device, kind))
        except Exception as error:  # noqa: BLE001 - 모델 파일 손상 등, 사용자에게 보여 준다
            if log_fn:
                log_fn(f"오프라인 번역 모델을 불러오지 못했습니다: {error}")
    elif log_fn:
        log_fn("오프라인 번역 모델(M2M100)을 찾지 못했습니다.")
    if allow_online:
        chain.append(GoogleTranslator())
        chain.append(MyMemoryTranslator())
    if not chain:
        raise TranslationError(
            "사용할 수 있는 번역 엔진이 없습니다. 오프라인 번역 모델 폴더를 지정하거나 온라인 번역을 허용해 주세요."
        )
    return chain


def group_sentences(texts: Sequence[str], max_group: int = 4, max_chars: int = 240) -> list[tuple[int, int]]:
    """연속된 큐를 문장 단위로 묶는다. [(start_index, end_index_exclusive), ...]"""
    groups: list[tuple[int, int]] = []
    start = 0
    size = 0
    for i, text in enumerate(texts):
        size += len(text)
        count = i - start + 1
        if text.rstrip().endswith(SENTENCE_END) or count >= max_group or size >= max_chars:
            groups.append((start, i + 1))
            start, size = i + 1, 0
    if start < len(texts):
        groups.append((start, len(texts)))
    return groups


def distribute(translated: str, weights: Sequence[int], no_space: bool = False) -> list[str]:
    """번역된 문장을 원문 큐 길이 비율에 맞춰 조각으로 나눈다."""
    n = len(weights)
    if n == 1:
        return [translated]
    total = sum(weights) or n
    if no_space:
        # 글자 단위로 나누되 라틴 문자·숫자 덩어리(이름 등)는 한 단위로 묶어 중간이 끊기지 않게 한다.
        units = re.findall(r"[A-Za-z0-9À-ÿ]+|.", translated)
        joiner = ""
    else:
        units = translated.split(" ")
        joiner = " "
    if len(units) < n:
        return [translated] + [""] * (n - 1)
    pieces: list[str] = []
    pos = 0
    for i, w in enumerate(weights):
        if i == n - 1:
            take = len(units) - pos
        else:
            take = max(1, round(len(units) * w / total))
            take = min(take, len(units) - pos - (n - 1 - i))
        pieces.append(joiner.join(units[pos : pos + take]).strip())
        pos += take
    return pieces


def translate_texts(chain: Sequence[Engine], texts: Sequence[str], source: str, target: str, log_fn: LogFn = None) -> list[str]:
    """체인을 차례로 시도해 첫 성공 결과를 돌려준다."""
    errors: list[str] = []
    for engine in chain:
        try:
            result = engine.translate(texts, source, target)
            if len(result) != len(texts):
                raise TranslationError("번역 결과 줄 수가 원문과 다릅니다.")
        except Exception as error:  # noqa: BLE001 - 다음 엔진으로 넘어간다
            errors.append(f"{engine.name}: {error}")
            if log_fn:
                log_fn(f"{engine.name} 실패, 다음 엔진 시도: {error}")
            continue
        if log_fn:
            log_fn(f"번역 엔진: {engine.name} ({languages.display_name(source)} → {languages.display_name(target)})")
        return result
    raise TranslationError(" / ".join(errors) or "사용 가능한 번역 엔진이 없습니다.")


def translate_cues(chain: Sequence[Engine], texts: Sequence[str], source: str, target: str, log_fn: LogFn = None) -> list[str]:
    """문장 단위로 묶어 번역한 뒤 각 큐에 다시 배분한다. 결과 길이는 texts와 같다."""
    groups = group_sentences(texts)
    sentences = [" ".join(t.strip() for t in texts[a:b]) for a, b in groups]
    translated = translate_texts(chain, sentences, source, target, log_fn=log_fn)
    no_space = target in languages.NO_SPACE_LANGS
    out: list[str] = []
    for (a, b), sentence in zip(groups, translated, strict=True):
        out.extend(distribute(sentence, [max(1, len(t)) for t in texts[a:b]], no_space))
    return out
