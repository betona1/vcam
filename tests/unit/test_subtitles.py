"""자막(SRT) 핵심 로직: 타임스탬프, 분할, 읽기/쓰기, 파일 이름, 번역 묶기/배분, 엔진 체인, 파이프라인(가짜 모델)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from vcam.services.profile_service import ProfileService, Settings
from vcam.subtitles import languages, pipeline, translator
from vcam.subtitles import transcriber as tr
from vcam.subtitles.srt import (
    Cue,
    Word,
    language_of,
    parse_timestamp,
    read_srt,
    split_words,
    subtitle_path,
    timestamp,
    to_srt,
    write_srt,
)

# ── 타임스탬프 ────────────────────────────────────────────────────────────────


def test_timestamp_format_and_clamp():
    assert timestamp(0) == "00:00:00,000"
    assert timestamp(3661.5) == "01:01:01,500"
    assert timestamp(-1) == "00:00:00,000"


@pytest.mark.parametrize("value", [0.0, 1.234, 59.999, 3600.0, 7325.5])
def test_timestamp_roundtrip(value):
    assert parse_timestamp(timestamp(value)) == pytest.approx(value, abs=0.001)


# ── 긴 구간 분할 ──────────────────────────────────────────────────────────────


def words_for(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    out = []
    t = start
    for token in text.split(" "):
        out.append(Word(t, t + step, " " + token))
        t += step
    return out


def test_short_segment_untouched():
    cues = split_words(0, 2, "Hello world.", words_for("Hello world."))
    assert [c.text for c in cues] == ["Hello world."]


def test_long_segment_split_keeps_all_words():
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    cues = split_words(0, 20, text, words_for(text), max_duration=5.0, max_chars=30)
    assert len(cues) > 1
    for cue in cues:
        assert len(cue.text) <= 30 * 2  # 짧은 조각 병합 허용치
        assert cue.start < cue.end
    assert " ".join(c.text for c in cues) == text


def test_hyphenated_word_not_split():
    words = [Word(0, 0.5, " released"), Word(0.5, 1.0, " on"), Word(1.0, 1.5, " that"),
             Word(1.5, 2.0, " follow"), Word(2.0, 2.5, "-through"), Word(2.5, 3.0, " today")]  # fmt: skip
    cues = split_words(0, 3, "released on that follow-through today", words, max_duration=1.0, max_chars=18)
    assert "follow-through" in " ".join(c.text for c in cues)
    assert not any(c.text.startswith("-") for c in cues)


def test_sentence_boundary_splits_first():
    text = "This is the first sentence. And here is the second one."
    cues = split_words(0, 10, text, words_for(text), max_duration=20, max_chars=30)
    assert cues[0].text == "This is the first sentence."


def test_empty_segment_gives_nothing():
    assert split_words(0, 1, "   ", []) == []


# ── 읽기/쓰기와 파일 이름 ─────────────────────────────────────────────────────


def test_write_read_roundtrip_with_bom(tmp_path):
    cues = [Cue(0.0, 1.5, "안녕하세요"), Cue(1.5, 3.0, "Hello\nWorld")]
    path = tmp_path / "a.srt"
    write_srt(path, cues)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not (tmp_path / "a.srt.tmp").exists()
    back = read_srt(path)
    assert [c.text for c in back] == ["안녕하세요", "Hello\nWorld"]
    assert back[1].end == pytest.approx(3.0)


def test_to_srt_numbering():
    text = to_srt([Cue(0, 1, "a"), Cue(1, 2, "b")])
    assert "1\n00:00:00,000 --> 00:00:01,000\na\n" in text
    assert "2\n00:00:01,000 --> 00:00:02,000\nb\n" in text
    assert to_srt([]) == ""


def test_subtitle_path_never_overwrites(tmp_path):
    video = tmp_path / "vcam_2026-10-30_12-00-00.mp4"
    video.write_bytes(b"")
    first = subtitle_path(video, "ko")
    assert first.name == "vcam_2026-10-30_12-00-00.ko.srt"
    first.write_text("x")
    second = subtitle_path(video, "ko")
    assert second.name == "vcam_2026-10-30_12-00-00.ko (2).srt"
    custom = subtitle_path(video, "en", tmp_path / "out")
    assert custom.parent.name == "out" and custom.parent.is_dir()


def test_language_of_filename():
    assert language_of(Path("video.ko.srt")) == "ko"
    assert language_of(Path("video.zh-TW (2).srt")) == "zh-TW"
    assert language_of(Path("my.video.srt")) is None  # 'video'는 언어 코드 길이가 아니다
    assert language_of(Path("plain.srt")) is None


# ── 언어표 ────────────────────────────────────────────────────────────────────


def test_language_lookup():
    assert languages.get("zh-TW").google == "zh-TW"
    assert languages.get("zh").whisper == "zh"
    assert languages.get("xx") is None
    assert languages.display_name("auto") == "자동 감지"
    assert languages.display_name("ja") == "일본어"
    for code in ("en", "ko", "zh", "ja", "es"):
        assert code in languages.BY_CODE


# ── 번역 묶기/배분/체인 ───────────────────────────────────────────────────────


class FakeEngine:
    name = "fake"

    def __init__(self, fail: bool = False, prefix: str = "T:") -> None:
        self.fail = fail
        self.prefix = prefix
        self.calls: list[list[str]] = []

    def translate(self, texts, source, target):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("boom")
        return [f"{self.prefix}{t}" for t in texts]


def test_group_sentences_by_punctuation_and_limits():
    texts = ["Hello there", "how are you.", "Fine.", "a", "b", "c", "d", "e"]
    groups = translator.group_sentences(texts, max_group=4)
    assert groups[0] == (0, 2)
    assert groups[1] == (2, 3)
    assert groups[2] == (3, 7)  # max_group
    assert groups[3] == (7, 8)


def test_distribute_by_weights_space_language():
    pieces = translator.distribute("one two three four five six", [1, 1], no_space=False)
    assert pieces == ["one two three", "four five six"]
    assert translator.distribute("solo", [5], no_space=False) == ["solo"]
    # 단어 수가 큐 수보다 적으면 첫 큐에 몰고 나머지는 빈칸
    assert translator.distribute("x y", [1, 1, 1], no_space=False) == ["x y", "", ""]


def test_distribute_cjk_keeps_latin_runs():
    pieces = translator.distribute("私はClaudeです", [1, 1], no_space=True)
    assert "".join(pieces) == "私はClaudeです"
    assert all("Claude" not in p or p.count("Claude") == 1 for p in pieces)
    assert not any(p.startswith("laude") for p in pieces)


def test_translate_cues_keeps_length_and_falls_back_to_next_engine():
    bad, good = FakeEngine(fail=True), FakeEngine()
    texts = ["Hello there", "how are you.", "Fine."]
    out = translator.translate_cues([bad, good], texts, "en", "ko")
    assert len(out) == len(texts)
    assert bad.calls and good.calls  # 첫 엔진 실패 후 다음 엔진 호출


def test_translate_texts_all_fail_raises():
    with pytest.raises(translator.TranslationError):
        translator.translate_texts([FakeEngine(fail=True)], ["a"], "en", "ko")


def test_build_chain_requires_offline_model_or_online_consent(tmp_path, monkeypatch):
    monkeypatch.delenv("VCAM_MODEL_DIR", raising=False)
    monkeypatch.setattr(translator, "model_roots", lambda extra=None: [tmp_path])
    with pytest.raises(translator.TranslationError):
        translator.build_chain(allow_online=False)
    chain = translator.build_chain(allow_online=True)
    assert [e.name for e in chain] == ["Google (온라인)", "MyMemory (온라인)"]


def test_find_offline_model_accepts_parent_or_model_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("VCAM_MODEL_DIR", raising=False)
    model = tmp_path / "m2m100_418M-ct2-int8"
    model.mkdir()
    (model / "model.bin").write_bytes(b"")
    (model / "sentencepiece.bpe.model").write_bytes(b"")
    assert translator.find_offline_model(tmp_path) == (model, "m2m100")
    assert translator.find_offline_model(model) == (model, "m2m100")
    monkeypatch.setattr(translator, "model_roots", lambda extra=None: [])
    assert translator.find_offline_model() is None


# ── 파이프라인 (가짜 모델, 가짜 디코더) ───────────────────────────────────────


@dataclass
class FakeWord:
    start: float
    end: float
    word: str


@dataclass
class FakeSegment:
    start: float
    end: float
    text: str
    words: list[FakeWord]


@dataclass
class FakeInfo:
    language: str
    language_probability: float
    duration: float


class FakeModel:
    def __init__(self, segments, language="en"):
        self.segments = segments
        self.language = language
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(self.segments), FakeInfo(self.language, 0.9, len(audio) / tr.SAMPLE_RATE)


@pytest.fixture
def fake_decode(monkeypatch):
    monkeypatch.setattr(tr, "decode_audio", lambda ffmpeg, path, cancel=None: np.zeros(tr.SAMPLE_RATE * 4, dtype=np.float32))


def segments_en():
    return [
        FakeSegment(0.0, 1.5, " Hello there.", [FakeWord(0.0, 0.7, " Hello"), FakeWord(0.7, 1.5, " there.")]),
        FakeSegment(1.5, 3.0, " How are you?", [FakeWord(1.5, 2.0, " How"), FakeWord(2.0, 2.5, " are"), FakeWord(2.5, 3.0, " you?")]),
        FakeSegment(3.0, 3.0, " ", []),  # 빈 구간은 버린다
    ]


def test_process_file_writes_original_and_translation(tmp_path, fake_decode):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    options = pipeline.SubtitleOptions(translate=True, targets=("ko", "en"))
    model = FakeModel(segments_en())
    events: list[tuple[str, float]] = []
    logs: list[str] = []
    result = pipeline.process_file(video, options, ffmpeg=None, model=model, chain=[FakeEngine()],
                                   progress=lambda s, f: events.append((s, f)), log_fn=logs.append)  # fmt: skip
    assert result.original == tmp_path / "clip.en.srt"
    assert result.cue_count == 2
    assert result.detected == "en"
    assert set(result.translated) == {"ko"}  # 원문과 같은 영어는 생략
    assert read_srt(result.translated["ko"])[0].text.startswith("T:")
    assert read_srt(result.original)[1].text == "How are you?"
    assert ("decode", 1.0) in events and ("transcribe", 1.0) in events and ("translate", 1.0) in events
    assert any("번역 생략" in line for line in logs)
    assert model.kwargs["language"] is None  # 자동 감지
    assert not result.errors


def test_process_file_passes_explicit_language_and_reports_partial_errors(tmp_path, fake_decode):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    options = pipeline.SubtitleOptions(source_language="zh-TW", translate=True, targets=("ko",))
    model = FakeModel(segments_en(), language="zh")
    result = pipeline.process_file(video, options, None, model, chain=[FakeEngine(fail=True)])
    assert model.kwargs["language"] == "zh"  # 번체는 Whisper 코드 zh로
    assert result.original.name == "clip.zh.srt"
    assert result.translated == {}
    assert result.errors and "한국어 번역 실패" in result.errors[0]


def test_process_file_without_speech_raises(tmp_path, fake_decode):
    video = tmp_path / "quiet.mp4"
    video.write_bytes(b"")
    with pytest.raises(tr.SubtitleError, match="인식된 대사가 없습니다"):
        pipeline.process_file(video, pipeline.SubtitleOptions(), None, FakeModel([]))
    assert not list(tmp_path.glob("*.srt"))


def test_process_file_cancel_between_segments(tmp_path, fake_decode):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    with pytest.raises(tr.Cancelled):
        pipeline.process_file(video, pipeline.SubtitleOptions(), None, FakeModel(segments_en()), cancel=lambda: True)


def test_process_file_missing_source(tmp_path):
    with pytest.raises(tr.SubtitleError, match="찾을 수 없습니다"):
        pipeline.process_file(tmp_path / "nope.mp4", pipeline.SubtitleOptions(), None, FakeModel([]))


def test_run_job_reports_each_file_and_uses_cache(tmp_path, fake_decode, monkeypatch):
    videos = []
    for name in ("a.mp4", "b.mp4"):
        p = tmp_path / name
        p.write_bytes(b"")
        videos.append(p)
    loads: list[tuple] = []

    def fake_load(name, device, allow_download, log_fn=None):
        loads.append((name, device))
        return FakeModel(segments_en()), "cpu"

    monkeypatch.setattr(tr, "load_model", fake_load)
    monkeypatch.setattr(tr, "default_model", lambda: "small")
    cache = pipeline.EngineCache()
    done: list[int] = []
    failed: list[tuple[int, str]] = []
    success, failure = pipeline.run_job(videos, pipeline.SubtitleOptions(), None, cache,
                                        on_file_done=lambda i, r: done.append(i), on_file_failed=lambda i, m: failed.append((i, m)))  # fmt: skip
    assert (success, failure) == (2, 0) and done == [0, 1] and failed == []
    assert loads == [("small", "auto")]
    # 두 번째 작업은 모델을 다시 불러오지 않는다
    pipeline.run_job(videos[:1], pipeline.SubtitleOptions(), None, cache)
    assert len(loads) == 1


def test_run_job_model_missing_marks_all_failed(tmp_path, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"")
    monkeypatch.setattr(tr, "is_model_cached", lambda name: False)
    monkeypatch.setattr(tr, "default_model", lambda: "small")
    monkeypatch.setattr(tr, "resolve_device", lambda choice="auto": ("cpu", "int8"))
    failed: list[str] = []
    success, failure = pipeline.run_job([video], pipeline.SubtitleOptions(allow_download=False), None,
                                        pipeline.EngineCache(), on_file_failed=lambda i, m: failed.append(m))  # fmt: skip
    assert (success, failure) == (0, 1)
    assert "PC에 없습니다" in failed[0]


def test_run_job_cancelled_before_start(tmp_path, fake_decode, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"")
    monkeypatch.setattr(tr, "load_model", lambda *a, **k: (FakeModel(segments_en()), "cpu"))
    monkeypatch.setattr(tr, "default_model", lambda: "small")
    failed: list[str] = []
    success, failure = pipeline.run_job([video], pipeline.SubtitleOptions(), None, pipeline.EngineCache(),
                                        on_file_failed=lambda i, m: failed.append(m), cancel=lambda: True)  # fmt: skip
    assert (success, failure) == (0, 1) and failed == ["취소됨"]


# ── 설정 저장 ─────────────────────────────────────────────────────────────────


def test_subtitle_settings_roundtrip_and_defaults(tmp_path):
    svc = ProfileService(tmp_path / "settings.json")
    s = svc.update(Settings(), subtitle_targets=("ko", "ja"), subtitle_model="small", subtitle_online=True)
    loaded = svc.load()
    assert loaded == s
    assert loaded.subtitle_targets == ("ko", "ja")  # JSON 목록이 튜플로 돌아온다
    assert Settings().subtitle_online is False and Settings().subtitle_download is False  # 네트워크 사용은 기본 OFF
    assert Settings().subtitle_targets == ("ko",)


def test_availability_reports_missing_package(monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "faster_whisper" else real(name))
    avail = tr.availability()
    assert not avail.ok and "vcam[subtitles]" in avail.reason
