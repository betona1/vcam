"""시스템 진단: OS, 모니터, FFmpeg, 인코더, 캡처, 오디오 장치, 저장 공간.

GUI 스레드에서 호출하지 않는다(인코더 시험에 수 초가 걸릴 수 있다).
"""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from vcam.domain.models import ENCODER_LABELS, Rect
from vcam.encoding.encoder_probe import ENCODER_ORDER, select_encoder
from vcam.encoding.ffmpeg import FfmpegPaths, ffmpeg_version, find_ffmpeg
from vcam.util.paths import format_bytes, free_bytes, is_writable_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbeItem:
    group: str
    name: str
    ok: bool | None  # None = 정보
    detail: str
    hint: str = ""


@dataclass
class ProbeReport:
    items: list[ProbeItem] = field(default_factory=list)
    ffmpeg: FfmpegPaths | None = None
    encoder: str = ""

    def add(self, *args, **kwargs) -> None:
        self.items.append(ProbeItem(*args, **kwargs))

    @property
    def can_record(self) -> bool:
        return self.ffmpeg is not None and bool(self.encoder)


def run_probe(ffmpeg_custom: str, output_dir: Path, encoder_preference: str = "auto") -> ProbeReport:
    report = ProbeReport()
    win = platform.win32_ver()
    report.add("시스템", "운영체제", None, f"Windows {win[0]} (빌드 {win[1]})")
    report.add("시스템", "Python", None, sys.version.split()[0])
    try:
        import PySide6

        report.add("시스템", "PySide6", None, PySide6.__version__)
    except ImportError:
        report.add("시스템", "PySide6", False, "없음")

    try:
        from vcam.capture.region import list_monitors

        for m in list_monitors():
            report.add("화면", f"모니터 {m.index}", True, f"{m.rect.width}×{m.rect.height} @ ({m.rect.left}, {m.rect.top})")
    except Exception as exc:  # noqa: BLE001 - 진단 결과로 표시
        report.add("화면", "모니터", False, str(exc))

    _probe_capture(report)

    ffmpeg = find_ffmpeg(ffmpeg_custom)
    report.ffmpeg = ffmpeg
    if ffmpeg is None:
        report.add("인코딩", "FFmpeg", False, "찾을 수 없음",
                   "설정에서 ffmpeg.exe 위치를 지정하거나 'winget install Gyan.FFmpeg'로 설치해 주세요.")  # fmt: skip
    else:
        report.add("인코딩", "FFmpeg", True, ffmpeg_version(ffmpeg))
        selection = select_encoder(ffmpeg, encoder_preference)
        tried = dict(selection.tried)
        for key in ENCODER_ORDER:
            if key in tried:
                report.add("인코딩", ENCODER_LABELS[key], tried[key], "사용 가능" if tried[key] else "사용 불가")
        report.encoder = selection.key
        if selection.key:
            report.add("인코딩", "선택된 인코더", True, ENCODER_LABELS[selection.key])
        else:
            report.add("인코딩", "선택된 인코더", False, "없음", "FFmpeg 빌드에 H.264 인코더가 있는지 확인해 주세요.")

    _probe_audio(report)

    writable = is_writable_dir(output_dir)
    report.add("저장", "저장 폴더 쓰기", writable, str(output_dir),
               "" if writable else "다른 저장 폴더를 선택해 주세요.")  # fmt: skip
    try:
        free = free_bytes(output_dir)
        report.add("저장", "여유 공간", free > 1024**3, format_bytes(free),
                   "" if free > 1024**3 else "1GB 이상 확보하는 것을 권장합니다.")  # fmt: skip
    except OSError as exc:
        report.add("저장", "여유 공간", False, str(exc))
    return report


def _probe_capture(report: ProbeReport) -> None:
    from vcam.capture.base import CaptureError
    from vcam.capture.dxcam_backend import DxcamBackend
    from vcam.capture.mss_backend import MssBackend

    test = Rect(0, 0, 64, 64)
    for label, backend in (("DXcam (고성능)", DxcamBackend()), ("GDI (호환)", MssBackend())):
        try:
            backend.open(test)
            ok = backend.grab() is not None
            report.add("캡처", label, ok, "동작 확인" if ok else "프레임 없음")
        except CaptureError as exc:
            report.add("캡처", label, False, str(exc))
        finally:
            backend.close()


def _probe_audio(report: ProbeReport) -> None:
    """장치 목록과 함께 기본 출력 장치 루프백을 실제로 0.2초 열어 본다(마이크는 개인정보 때문에 열지 않는다)."""
    from vcam.audio.base import AudioError
    from vcam.audio.wasapi_backend import WasapiSource, list_devices

    try:
        speakers, mics = list_devices()
    except Exception as exc:  # noqa: BLE001 - 진단 결과로 표시
        report.add("오디오", "장치 검색", False, str(exc))
        return
    default_speaker = next((d.name for d in speakers if d.is_default), "")
    report.add("오디오", "출력 장치", bool(speakers), f"{len(speakers)}개 · 기본: {default_speaker or '없음'}",
               "" if speakers else "스피커나 헤드셋을 연결해 주세요.")  # fmt: skip
    default_mic = next((d.name for d in mics if d.is_default), "")
    report.add("오디오", "마이크", bool(mics) or None, f"{len(mics)}개 · 기본: {default_mic or '없음'}",
               "" if mics else "마이크 녹음을 쓰려면 마이크를 연결해 주세요.")  # fmt: skip
    if not speakers:
        return
    source = WasapiSource("system")
    try:
        source.open()
        frames = sum(source.read().shape[0] for _ in range(10))
        report.add("오디오", "시스템 소리 녹음", frames > 0, f"동작 확인 ({source.samplerate} Hz, {source.channels}ch)")
    except AudioError as exc:
        report.add("오디오", "시스템 소리 녹음", False, str(exc))
    finally:
        source.close()
