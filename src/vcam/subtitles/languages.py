"""지원 언어 표. 엔진별 언어 코드를 한곳에서 관리한다."""

from __future__ import annotations

from dataclasses import dataclass

AUTO = "auto"


@dataclass(frozen=True)
class Language:
    code: str  # 내부 코드 (Whisper 코드 기준)
    name_ko: str  # UI 표시 이름
    google: str  # Google 번역 코드
    mymemory: str  # MyMemory 코드
    whisper: str  # Whisper 언어 코드 (zh-TW 등은 zh로 묶임)

    @property
    def m2m(self) -> str:
        """M2M100 언어 코드 (Whisper 코드와 같고, 번체 중국어는 zh로 묶임)."""
        return self.whisper


LANGUAGES: tuple[Language, ...] = (
    Language("ko", "한국어", "ko", "ko-KR", "ko"),
    Language("en", "영어", "en", "en-US", "en"),
    Language("ja", "일본어", "ja", "ja-JP", "ja"),
    Language("zh", "중국어(간체)", "zh-CN", "zh-CN", "zh"),
    Language("zh-TW", "중국어(번체)", "zh-TW", "zh-TW", "zh"),
    Language("es", "스페인어", "es", "es-ES", "es"),
    Language("fr", "프랑스어", "fr", "fr-FR", "fr"),
    Language("de", "독일어", "de", "de-DE", "de"),
    Language("pt", "포르투갈어", "pt", "pt-PT", "pt"),
    Language("it", "이탈리아어", "it", "it-IT", "it"),
    Language("ru", "러시아어", "ru", "ru-RU", "ru"),
    Language("vi", "베트남어", "vi", "vi-VN", "vi"),
    Language("th", "태국어", "th", "th-TH", "th"),
    Language("id", "인도네시아어", "id", "id-ID", "id"),
    Language("ar", "아랍어", "ar", "ar-SA", "ar"),
    Language("hi", "힌디어", "hi", "hi-IN", "hi"),
    Language("tr", "터키어", "tr", "tr-TR", "tr"),
)

BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}

# 띄어쓰기를 쓰지 않는 언어: 번역문을 큐에 배분할 때 글자 단위로 나눈다.
NO_SPACE_LANGS = frozenset({"ja", "zh", "zh-TW", "th"})


def get(code: str) -> Language | None:
    """내부 코드 또는 Whisper 감지 코드로 Language를 찾는다. 없으면 None."""
    if code in BY_CODE:
        return BY_CODE[code]
    return next((lang for lang in LANGUAGES if lang.whisper == code), None)


def display_name(code: str) -> str:
    if code == AUTO:
        return "자동 감지"
    lang = get(code)
    return lang.name_ko if lang else code
