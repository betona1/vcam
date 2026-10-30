"""사용자 설정 저장/불러오기. 스키마 버전을 두고 마이그레이션한다."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from vcam.domain.models import RecordingProfile, Rect
from vcam.util.paths import DEFAULT_FILENAME_TEMPLATE, app_data_dir, default_output_dir

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3


@dataclass(frozen=True)
class Settings:
    schema_version: int = SCHEMA_VERSION
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    ffmpeg_path: str = ""
    capture_backend: str = "auto"  # auto | dxcam | mss
    countdown_s: int = 3
    minimize_on_record: bool = True
    show_guide_frame: bool = True
    theme: str = "system"  # system | light | dark
    first_run_notice_ack: bool = False
    auto_update: bool = True  # 시작할 때 GitHub 릴리스 확인 후 백그라운드 다운로드
    source_kind: str = "display"  # display | region
    monitor_index: int = 1
    last_region: tuple[int, int, int, int] | None = None
    system_audio_device: str = ""  # 빈 문자열 = 기본 출력 장치
    mic_device: str = ""  # 빈 문자열 = 기본 마이크
    editor_same_folder: bool = True  # 편집기: 결과를 원본과 같은 폴더에 저장. 끄면 output_dir 사용
    # 자막 만들기(SRT) 마지막 선택값
    subtitle_language: str = "auto"
    subtitle_model: str = ""  # 빈 문자열 = 받아 둔 모델 중 자동 선택
    subtitle_device: str = "auto"  # auto | cuda | cpu
    subtitle_download: bool = False  # 모델이 없으면 HuggingFace에서 내려받기 허용
    subtitle_translate: bool = False
    subtitle_targets: tuple[str, ...] = ("ko",)
    subtitle_online: bool = False  # 온라인 번역(Google·MyMemory) 허용 — 자막 본문이 외부로 전송됨
    subtitle_model_dir: str = ""  # 오프라인 번역 모델 폴더
    subtitle_max_chars: int = 45
    profile: RecordingProfile = field(default_factory=RecordingProfile)

    @property
    def last_region_rect(self) -> Rect | None:
        return Rect(*self.last_region) if self.last_region else None


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """이전 스키마 데이터를 현재 스키마로 올린다."""
    version = int(data.get("schema_version", 1))
    if version < 2:
        # v1은 fps/quality를 최상위에 두었다.
        profile = dict(data.get("profile") or {})
        for key, target in (("fps", "fps"), ("quality", "quality_preset"), ("encoder", "encoder_preference")):
            if key in data:
                profile.setdefault(target, data.pop(key))
        data["profile"] = profile
        version = 2
    if version < 3:
        # v2까지는 소리 녹음이 없어 system_audio_enabled=False가 의미 없는 기본값이었다.
        profile = dict(data.get("profile") or {})
        profile["system_audio_enabled"] = True
        profile.setdefault("microphone_enabled", False)
        data["profile"] = profile
        version = 3
    data["schema_version"] = version
    return data


def _from_dict(data: dict[str, Any]) -> Settings:
    data = migrate(dict(data))
    known = {f.name for f in fields(Settings)}
    values = {k: v for k, v in data.items() if k in known}
    profile_known = {f.name for f in fields(RecordingProfile)}
    values["profile"] = RecordingProfile(
        **{k: v for k, v in (data.get("profile") or {}).items() if k in profile_known}
    )
    if values.get("last_region") is not None:
        values["last_region"] = tuple(int(v) for v in values["last_region"])
    if "subtitle_targets" in values:
        values["subtitle_targets"] = tuple(str(v) for v in (values["subtitle_targets"] or ()))
    return Settings(**values)


class ProfileService:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "settings.json"

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            return _from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("설정 파일을 읽지 못해 기본값을 사용합니다: %s", exc)
            backup = self.path.with_suffix(".broken.json")
            try:
                os.replace(self.path, backup)
            except OSError:
                log.warning("손상된 설정 파일을 백업하지 못했습니다", exc_info=True)
            return Settings()

    def save(self, settings: Settings) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, settings: Settings, **changes: Any) -> Settings:
        new = replace(settings, **changes)
        self.save(new)
        return new
