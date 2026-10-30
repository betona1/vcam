"""SRT 자막 데이터 구조, 읽기/쓰기, 긴 구간 분할, 결과 파일 이름."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vcam.util.paths import unique_path

SENTENCE_END = (".", "?", "!", "。", "？", "！", "…")
# `영상.ko.srt`, `영상.zh-TW (2).srt` 처럼 vCAM이 만든 자막 파일 이름에서 언어 코드를 읽는다.
_LANG_SUFFIX = re.compile(r"\.([A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?)(?: \(\d+\))?$")


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    word: str


def timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def parse_timestamp(text: str) -> float:
    h, m, rest = text.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def split_words(
    start: float,
    end: float,
    text: str,
    words: list[Word] | None,
    max_duration: float = 5.0,
    max_chars: int = 45,
    min_chars: int = 12,
) -> list[Cue]:
    """단어 타임스탬프를 기준으로 긴 구간을 자막용 짧은 큐로 나눈다."""
    text = text.strip()
    words = [w for w in (words or []) if w.word.strip()]
    if not words or (end - start <= max_duration and len(text) <= max_chars):
        return [Cue(start, end, text)] if text else []
    chunks: list[Cue] = []
    chunk: list[Word] = []

    def flush() -> None:
        if chunk:
            chunks.append(Cue(chunk[0].start, chunk[-1].end, "".join(x.word for x in chunk).strip()))
            chunk.clear()

    for word in words:
        candidate = "".join(x.word for x in [*chunk, word]).strip()
        duration = word.end - (chunk[0].start if chunk else word.start)
        # 공백 없이 이어지는 토큰(하이픈 단어, CJK 문자열 등) 앞에서는 끊지 않는다.
        continuation = bool(chunk) and not word.word.startswith(" ")
        if chunk and not continuation and (len(candidate) > max_chars or duration > max_duration):
            flush()
        chunk.append(word)
        current = "".join(x.word for x in chunk).strip()
        if current.endswith(SENTENCE_END) and len(current) >= min_chars:
            flush()
    flush()
    # 너무 짧은 조각은 앞 큐에 합친다.
    merged: list[Cue] = []
    for cue in chunks:
        if merged and len(cue.text) < min_chars and len(merged[-1].text) + 1 + len(cue.text) <= max_chars * 2:
            prev = merged[-1]
            merged[-1] = Cue(prev.start, cue.end, f"{prev.text} {cue.text}")
        else:
            merged.append(cue)
    return [c for c in merged if c.text and c.end > c.start]


def to_srt(cues: list[Cue]) -> str:
    lines = [f"{index}\n{timestamp(cue.start)} --> {timestamp(cue.end)}\n{cue.text}\n" for index, cue in enumerate(cues, 1)]
    return "\n".join(lines) + ("\n" if lines else "")


def write_srt(path: Path, cues: list[Cue]) -> None:
    """임시 파일에 쓴 뒤 원자적으로 바꾼다. BOM을 넣어 Windows 플레이어가 UTF-8로 읽게 한다."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(to_srt(cues), encoding="utf-8-sig")
    tmp.replace(path)


def read_srt(path: Path) -> list[Cue]:
    text = Path(path).read_text(encoding="utf-8-sig")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = block.strip().splitlines()
        if len(rows) < 2:
            continue
        timing_row = 1 if "-->" in rows[1] else 0
        timing = rows[timing_row]
        start, end = (parse_timestamp(part) for part in timing.split("-->"))
        cues.append(Cue(start, end, "\n".join(rows[timing_row + 1 :]).strip()))
    return cues


def subtitle_path(video: Path, lang: str, directory: Path | None = None) -> Path:
    """`영상이름.<언어>.srt` 형태로 덮어쓰지 않는 경로를 확보한다 (겹치면 ` (2)`)."""
    video = Path(video)
    target_dir = Path(directory) if directory else video.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    return unique_path(target_dir, f"{video.stem}.{lang}", ".srt")


def language_of(path: Path) -> str | None:
    """자막 파일 이름 꼬리표(`.ko`)에서 언어 코드를 읽는다. 없으면 None."""
    match = _LANG_SUFFIX.search(Path(path).stem)
    return match.group(1) if match else None
