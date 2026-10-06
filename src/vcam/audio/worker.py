"""오디오 소스 하나를 읽는 스레드. 녹화 전에는 음량 미터만, 녹화 중에는 PCM 기록까지 한다.

장치가 분리되거나 기본 장치가 바뀌면 제한된 횟수만 다시 연다(무제한 재시도 금지).
다시 여는 동안 빠진 구간은 AlignedPcmWriter가 무음으로 메운다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import StrEnum

from vcam.audio.base import AudioError, AudioKind, AudioSource
from vcam.audio.meter import LevelMeter
from vcam.audio.track_writer import AlignedPcmWriter

log = logging.getLogger(__name__)

MAX_REOPEN_ATTEMPTS = 5
REOPEN_DELAY_S = 1.0
STABLE_RESET_S = 30.0

SourceFactory = Callable[[], AudioSource]


class WorkerStatus(StrEnum):
    STARTING = "starting"
    OK = "ok"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPED = "stopped"


class AudioWorker:
    def __init__(self, kind: AudioKind, factory: SourceFactory) -> None:
        self.kind = kind
        self._factory = factory
        self.meter = LevelMeter()
        self._lock = threading.Lock()
        self._sink: AlignedPcmWriter | None = None
        self._stop = threading.Event()
        self._status = WorkerStatus.STARTING
        self._error = ""
        self.muted = False
        self.device_name = ""
        self.samplerate = 0
        self.channels = 0
        self._opened = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"vcam-audio-{kind}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait_opened(self, timeout: float) -> bool:
        """장치가 열리거나 최종 실패할 때까지 기다린다. 열렸으면 True."""
        self._opened.wait(timeout)
        return self._status is WorkerStatus.OK

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)

    @property
    def status(self) -> WorkerStatus:
        return self._status

    @property
    def error(self) -> str:
        return self._error

    def attach(self, sink: AlignedPcmWriter) -> None:
        with self._lock:
            self._sink = sink

    def detach(self) -> AlignedPcmWriter | None:
        with self._lock:
            sink, self._sink = self._sink, None
            return sink

    def _run(self) -> None:
        attempts = 0
        while not self._stop.is_set():
            source = self._factory()
            try:
                source.open()
            except AudioError as exc:
                attempts += 1
                self._error = str(exc)
                if attempts >= MAX_REOPEN_ATTEMPTS:
                    log.error("%s 오디오 장치를 열 수 없어 포기합니다: %s", self.kind, exc)
                    self._status = WorkerStatus.FAILED
                    self._opened.set()
                    return
                self._status = WorkerStatus.RECONNECTING
                self._stop.wait(REOPEN_DELAY_S)
                continue
            self.device_name = source.device_name
            self.samplerate, self.channels = source.samplerate, source.channels
            self._status = WorkerStatus.OK
            self._error = ""
            self._opened.set()
            opened_at = time.monotonic()
            try:
                self._pump(source)
            except AudioError as exc:
                # 한동안 잘 동작했다면 새 장애로 보고 횟수를 초기화한다. 계속 실패하는 장치는 여전히 포기한다.
                if time.monotonic() - opened_at >= STABLE_RESET_S:
                    attempts = 0
                attempts += 1
                self._error = str(exc)
                log.warning("%s 오디오 장치 오류(%d/%d): %s", self.kind, attempts, MAX_REOPEN_ATTEMPTS, exc)
                self.meter.reset()
                if attempts >= MAX_REOPEN_ATTEMPTS:
                    self._status = WorkerStatus.FAILED
                    return
                self._status = WorkerStatus.RECONNECTING
                self._stop.wait(REOPEN_DELAY_S)
            finally:
                source.close()
        self._status = WorkerStatus.STOPPED

    def _pump(self, source: AudioSource) -> None:
        while not self._stop.is_set():
            block = source.read()
            if self.muted:
                block = block * 0.0
            self.meter.update(block)
            with self._lock:
                sink = self._sink
            if sink is not None:
                sink.write(block)
