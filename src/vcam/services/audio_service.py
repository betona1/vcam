"""시스템 소리·마이크 작업 스레드를 설정에 맞춰 관리한다(Qt 비의존).

녹화 전에는 음량 미터용으로 장치를 열어 두고, 녹화가 시작되면 세션 시계에 맞춘
PCM 기록기를 붙인다. 마이크는 사용자가 켰을 때만 연다(개인정보).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from vcam.audio.base import AudioKind, AudioSource
from vcam.audio.meter import Level
from vcam.audio.track_writer import AlignedPcmWriter, AudioTrack, TrackStats
from vcam.audio.worker import AudioWorker, WorkerStatus
from vcam.util.clock import SessionClock

log = logging.getLogger(__name__)

TRACK_LABELS: dict[AudioKind, str] = {"system": "시스템 소리", "microphone": "마이크"}


@dataclass(frozen=True)
class AudioConfig:
    system_enabled: bool = True
    system_device: str = ""
    mic_enabled: bool = False
    mic_device: str = ""

    def wanted(self) -> dict[AudioKind, str]:
        result: dict[AudioKind, str] = {}
        if self.system_enabled:
            result["system"] = self.system_device
        if self.mic_enabled:
            result["microphone"] = self.mic_device
        return result


def _default_factory(kind: AudioKind, device_id: str) -> AudioSource:
    from vcam.audio.wasapi_backend import WasapiSource

    return WasapiSource(kind, device_id)


class AudioService:
    def __init__(self, source_factory: Callable[[AudioKind, str], AudioSource] = _default_factory) -> None:
        self._factory = source_factory
        self._workers: dict[AudioKind, tuple[str, AudioWorker]] = {}
        self._writers: list[tuple[AudioWorker, AlignedPcmWriter]] = []
        self._mic_muted = False

    # ── 설정 ───────────────────────────────────────────────────────────────
    def configure(self, config: AudioConfig) -> None:
        """필요한 작업 스레드만 남기고 시작/정지한다. 정지는 기다리지 않는다(GUI 스레드 보호)."""
        wanted = config.wanted()
        for kind in list(self._workers):
            device, worker = self._workers[kind]
            if kind not in wanted or wanted[kind] != device or worker.status is WorkerStatus.FAILED:
                worker.stop(timeout=0)
                del self._workers[kind]
        for kind, device in wanted.items():
            if kind not in self._workers:
                worker = AudioWorker(kind, lambda k=kind, d=device: self._factory(k, d))
                worker.muted = kind == "microphone" and self._mic_muted
                worker.start()
                self._workers[kind] = (device, worker)

    def worker(self, kind: AudioKind) -> AudioWorker | None:
        entry = self._workers.get(kind)
        return entry[1] if entry else None

    def level(self, kind: AudioKind) -> Level | None:
        w = self.worker(kind)
        return w.meter.read() if w else None

    def status(self, kind: AudioKind) -> WorkerStatus | None:
        w = self.worker(kind)
        return w.status if w else None

    def set_mic_muted(self, muted: bool) -> None:
        self._mic_muted = muted
        w = self.worker("microphone")
        if w:
            w.muted = muted

    @property
    def mic_muted(self) -> bool:
        return self._mic_muted

    # ── 녹화 ───────────────────────────────────────────────────────────────
    def wait_ready(self, timeout: float) -> list[str]:
        """작업 스레드에서 호출: 켜진 장치가 열릴 때까지 기다리고, 열지 못한 장치 이름을 돌려준다."""
        failed = []
        for kind, (_device, worker) in list(self._workers.items()):
            if not worker.wait_opened(timeout):
                failed.append(f"{TRACK_LABELS[kind]}: {worker.error or '장치를 열 수 없음'}")
        return failed

    def begin_recording(self, clock: SessionClock, session_dir: Path) -> list[AudioTrack]:
        self.abort_recording()  # 이전 녹화의 기록기가 남아 있으면 닫는다
        tracks = []
        for kind, (_device, worker) in self._workers.items():
            if worker.status is not WorkerStatus.OK or not worker.samplerate:
                log.warning("%s 장치가 준비되지 않아 이 트랙은 녹음하지 않습니다", kind)
                continue
            track = AudioTrack(kind, session_dir / f"{kind}.pcm", worker.samplerate, worker.channels)
            writer = AlignedPcmWriter(track, clock)
            worker.attach(writer)
            self._writers.append((worker, writer))
            tracks.append(track)
        return tracks

    def end_recording(self, duration_s: float) -> list[TrackStats]:
        stats = []
        for worker, writer in self._writers:
            worker.detach()
            stats.append(writer.finish(duration_s))
        self._writers = []
        return stats

    def abort_recording(self) -> None:
        for worker, writer in self._writers:
            worker.detach()
            writer.abort()
        self._writers = []

    def shutdown(self) -> None:
        self.abort_recording()
        for _device, worker in self._workers.values():
            worker.stop(timeout=1.0)
        self._workers.clear()
