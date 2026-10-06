"""인코딩 프리셋과 편집 프로젝트(.vcamproj) 저장·불러오기."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from vcam.editing.formats import EncodeSettings
from vcam.editing.segments import Segment
from vcam.util.paths import app_data_dir

log = logging.getLogger(__name__)

PROJECT_EXT = ".vcamproj"
PROJECT_VERSION = 1

BUILTIN_PRESETS: dict[str, EncodeSettings] = {
    "MP4 · H.264 균형 (기본)": EncodeSettings(),
    "MP4 · H.264 작은 용량 720p": EncodeSettings(resolution="preset", width=1280, height=720, quality=60),
    "MP4 · H.265 고화질": EncodeSettings(video_codec="hevc", quality=90),
    "MKV · H.264 고화질": EncodeSettings(container="mkv", quality=90),
    "WebM · VP9 (웹용)": EncodeSettings(container="webm", video_codec="vp9", audio_codec="opus"),
    "AVI · Xvid (호환성)": EncodeSettings(container="avi", video_codec="xvid", audio_codec="mp3"),
    "MOV · H.264 (편집용)": EncodeSettings(container="mov", quality=90),
    "WMV (Windows)": EncodeSettings(container="wmv", video_codec="wmv2", audio_codec="wma"),
    "GIF 애니메이션 (480px, 15fps)": EncodeSettings(container="gif", video_codec="gif", resolution="fit_width", width=480, fps=15),
}


def _presets_path() -> Path:
    return app_data_dir() / "encode_presets.json"


def load_user_presets() -> dict[str, EncodeSettings]:
    path = _presets_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {name: EncodeSettings.from_dict(v) for name, v in data.items()}
    except (OSError, ValueError, TypeError):
        log.warning("인코딩 프리셋 파일을 읽지 못했습니다", exc_info=True)
        return {}


def save_user_preset(name: str, settings: EncodeSettings) -> None:
    presets = {k: v.to_dict() for k, v in load_user_presets().items()}
    presets[name] = settings.to_dict()
    _write_json(_presets_path(), presets)


def delete_user_preset(name: str) -> None:
    presets = {k: v.to_dict() for k, v in load_user_presets().items() if k != name}
    _write_json(_presets_path(), presets)


def _write_json(path: Path, data: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class ProjectFile:
    """편집 상태: 파일 목록, 파일별 구간, 작업 종류와 옵션."""

    files: list[Path] = field(default_factory=list)
    segments: dict[str, list[Segment]] = field(default_factory=dict)  # 파일 경로 → 구간
    split_points: dict[str, list[float]] = field(default_factory=dict)
    tool: str = "cut"
    mode: str = "fast"
    encode: EncodeSettings = field(default_factory=EncodeSettings)
    options: dict = field(default_factory=dict)

    def save(self, path: Path) -> None:
        _write_json(path, {
            "version": PROJECT_VERSION,
            "files": [str(f) for f in self.files],
            "segments": {k: [[s.start, s.end] for s in v] for k, v in self.segments.items()},
            "split_points": self.split_points,
            "tool": self.tool,
            "mode": self.mode,
            "encode": self.encode.to_dict(),
            "options": self.options,
        })  # fmt: skip

    @classmethod
    def load(cls, path: Path) -> ProjectFile:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            files=[Path(f) for f in data.get("files", [])],
            segments={k: [Segment(float(a), float(b)) for a, b in v] for k, v in data.get("segments", {}).items()},
            split_points={k: [float(x) for x in v] for k, v in data.get("split_points", {}).items()},
            tool=str(data.get("tool", "cut")),
            mode=str(data.get("mode", "fast")),
            encode=EncodeSettings.from_dict(data.get("encode", {})),
            options=dict(data.get("options", {})),
        )
