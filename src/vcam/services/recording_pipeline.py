"""Qt에 의존하지 않는 녹화 엔진: 캡처 스레드 → 제한된 큐 → 인코더 스레드.

- 캡처 스레드는 세션 시계 기준으로 프레임 슬롯마다 한 장을 잡는다.
- 늦어진 슬롯과 큐가 가득 차서 버린 프레임은 인코더 스레드가 직전 프레임을 반복해 메운다.
  덕분에 결과 시간축은 실제 경과 시간과 같고, 드롭 수는 메트릭으로 남는다.
- 일시정지 중에는 시계가 멈추므로 재개 후 타임스탬프가 연속된다.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vcam.capture.base import CaptureBackend, CaptureError
from vcam.domain.events import UserFacingError
from vcam.domain.models import RecordingMetrics, Rect
from vcam.encoding.encoder_probe import HARDWARE_ENCODERS
from vcam.encoding.ffmpeg import FfmpegPaths
from vcam.encoding.video_writer import EncoderError, FfmpegVideoWriter
from vcam.platform.windows import power
from vcam.util.clock import SessionClock, due_frame_index, slot_start_ns
from vcam.util.paths import free_bytes

log = logging.getLogger(__name__)

QUEUE_BYTE_BUDGET = 256 * 1024 * 1024
DISK_CHECK_INTERVAL_S = 5.0
HW_RETRY_WINDOW_NS = 2_000_000_000

BackendOpener = Callable[[Rect], CaptureBackend]
WriterFactory = Callable[[str], FfmpegVideoWriter]


@dataclass(frozen=True)
class PipelineConfig:
    rect: Rect
    fps: int
    encoder_key: str
    quality: str
    ffmpeg: FfmpegPaths
    partial_path: Path
    ffmpeg_log: Path
    min_free_bytes: int = 500 * 1024 * 1024


@dataclass(frozen=True)
class PipelineOutcome:
    frames_written: int
    frames_dropped: int
    encoder: str
    encoder_exit_code: int
    error: UserFacingError | None
    notes: tuple[str, ...]


class RecordingPipeline:
    def __init__(
        self,
        config: PipelineConfig,
        open_backend: BackendOpener,
        on_fatal: Callable[[UserFacingError], None],
        writer_factory: WriterFactory | None = None,
        clock: SessionClock | None = None,
    ) -> None:
        self.config = config
        self._open_backend = open_backend
        self._on_fatal = on_fatal
        self._writer_factory = writer_factory or self._default_writer
        self.clock = clock or SessionClock()

        frame_bytes = max(1, config.rect.width * config.rect.height * 4)
        self._queue: queue.Queue[tuple[int, np.ndarray]] = queue.Queue(
            maxsize=max(2, min(16, QUEUE_BYTE_BUDGET // frame_bytes))
        )
        self._stop = threading.Event()
        self._capture_done = threading.Event()
        self._lock = threading.Lock()
        self._frames_written = 0
        self._dropped = 0
        self._encoder = config.encoder_key
        self._error: UserFacingError | None = None
        self._notes: list[str] = []
        self._fps_window: deque[int] = deque(maxlen=64)
        self._writer: FfmpegVideoWriter | None = None
        self._exit_code = -1
        self._backend_name = ""
        self._ready = threading.Event()
        self._capture_thread = threading.Thread(target=self._capture_loop, name="vcam-capture", daemon=True)
        self._encode_thread = threading.Thread(target=self._encode_loop, name="vcam-encode", daemon=True)

    # ── 제어 ────────────────────────────────────────────────────────────────
    def start(self) -> None:
        """인코더를 띄우고 작업 스레드를 시작한다. 세션 시계는 캡처 백엔드가 열린 뒤 시작된다."""
        self._writer = self._writer_factory(self.config.encoder_key)
        self._writer.start()
        self._encode_thread.start()
        self._capture_thread.start()

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def pause(self) -> None:
        self.clock.pause()

    def resume(self) -> None:
        self.clock.resume()

    def stop(self, timeout: float = 60.0) -> PipelineOutcome:
        # 시계를 먼저 멈춰, 인코더가 마무리되는 동안 오디오가 영상보다 길게 기록되지 않게 한다.
        self.clock.pause()
        self._stop.set()
        self._capture_thread.join(timeout=10)
        self._encode_thread.join(timeout=timeout)
        if self._writer is not None:
            self._exit_code = self._writer.close()
        with self._lock:
            if self._exit_code != 0 and self._error is None and self._frames_written > 0:
                self._notes.append(f"FFmpeg 종료 코드 {self._exit_code}")
            return PipelineOutcome(
                frames_written=self._frames_written,
                frames_dropped=self._dropped,
                encoder=self._encoder,
                encoder_exit_code=self._exit_code,
                error=self._error,
                notes=tuple(self._notes),
            )

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def metrics(self) -> RecordingMetrics:
        with self._lock:
            window = list(self._fps_window)
            fps = 0.0
            if len(window) >= 2 and window[-1] > window[0]:
                fps = (len(window) - 1) * 1e9 / (window[-1] - window[0])
            return RecordingMetrics(
                elapsed_ns=self.clock.active_ns(),
                frames_written=self._frames_written,
                frames_dropped=self._dropped,
                measured_fps=fps,
                encoder=self._encoder,
            )

    # ── 내부 ────────────────────────────────────────────────────────────────
    def _default_writer(self, encoder_key: str) -> FfmpegVideoWriter:
        c = self.config
        return FfmpegVideoWriter(
            c.ffmpeg, c.rect.width, c.rect.height, c.fps, encoder_key, c.quality, c.partial_path, c.ffmpeg_log
        )

    def _fail(self, error: UserFacingError) -> None:
        with self._lock:
            if self._error is not None:
                return
            self._error = error
        log.error("녹화 치명적 오류: %s (%s)", error.message, error.detail)
        self._stop.set()
        self._on_fatal(error)

    def _capture_loop(self) -> None:
        fps = self.config.fps
        backend: CaptureBackend | None = None
        power.keep_awake()
        try:
            try:
                backend = self._open_backend(self.config.rect)
            except CaptureError as exc:
                self._fail(UserFacingError("capture_open", "화면 캡처를 시작할 수 없습니다.", str(exc)))
                return
            self._backend_name = backend.name
            log.info("캡처 백엔드: %s, 영역 %s, %dfps", backend.name, self.config.rect, fps)
            first = backend.grab()
            self.clock.start()
            self._ready.set()
            slot = 0
            last = first
            next_disk_check = time.monotonic() + DISK_CHECK_INTERVAL_S
            while not self._stop.is_set():
                if self.clock.is_paused:
                    self._stop.wait(0.02)
                    continue
                active = self.clock.active_ns()
                due = due_frame_index(active, fps)
                if slot > due:
                    wait_ns = slot_start_ns(slot, fps) - active
                    self._stop.wait(min(max(wait_ns, 0) / 1e9, 0.05))
                    continue
                frame = backend.grab()
                if frame is None:
                    frame = last
                if frame is None:
                    continue
                last = frame
                with self._lock:
                    self._dropped += due - slot  # 늦어서 건너뛴 슬롯
                    self._fps_window.append(time.perf_counter_ns())
                try:
                    self._queue.put_nowait((due, frame))
                except queue.Full:
                    with self._lock:
                        self._dropped += 1
                slot = due + 1
                if time.monotonic() >= next_disk_check:
                    next_disk_check = time.monotonic() + DISK_CHECK_INTERVAL_S
                    if free_bytes(self.config.partial_path.parent) < self.config.min_free_bytes:
                        self._fail(UserFacingError(
                            "disk_low",
                            "디스크 여유 공간이 부족해 녹화를 안전하게 멈췄습니다. 공간을 확보한 뒤 다시 녹화해 주세요.",
                        ))  # fmt: skip
                        return
        except CaptureError as exc:
            self._fail(UserFacingError("capture_lost", "녹화 중 화면 캡처가 끊겼습니다.", str(exc)))
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위: 기록 후 복구 경로로 넘긴다
            log.exception("캡처 스레드 예외")
            self._fail(UserFacingError("capture_crash", "녹화 중 예기치 않은 오류가 발생했습니다.", repr(exc)))
        finally:
            self._ready.set()
            self._capture_done.set()
            if backend is not None:
                backend.close()
            power.allow_sleep()

    def _encode_loop(self) -> None:
        try:
            self._encode_loop_inner()
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위: 기록 후 복구 경로로 넘긴다
            log.exception("인코더 스레드 예외")
            self._fail(UserFacingError("encoder_crash", "영상 저장 중 예기치 않은 오류가 발생했습니다.", repr(exc)))

    def _encode_loop_inner(self) -> None:
        next_index = 0
        last: np.ndarray | None = None
        retried = False
        while True:
            try:
                index, frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._capture_done.is_set() or (self._stop.is_set() and self._error is not None):
                    break
                continue
            try:
                filler = last if last is not None else frame
                while next_index < index:
                    self._writer.write(filler)
                    next_index += 1
                    with self._lock:
                        self._frames_written += 1
                self._writer.write(frame)
                next_index = index + 1
                last = frame
                with self._lock:
                    self._frames_written += 1
            except EncoderError as exc:
                elapsed = self.clock.active_ns()
                if not retried and self._encoder in HARDWARE_ENCODERS and elapsed < HW_RETRY_WINDOW_NS:
                    retried = True
                    log.warning("하드웨어 인코더 %s 실패, x264로 한 번 재시도: %s", self._encoder, exc)
                    self._writer.close(timeout=5)
                    self._writer = self._writer_factory("x264")
                    try:
                        self._writer.start()
                    except EncoderError as exc2:
                        self._fail(UserFacingError("encoder", "영상 인코더를 시작하지 못했습니다.", str(exc2)))
                        break
                    with self._lock:
                        self._notes.append(f"{self._encoder} 실패로 x264로 전환")
                        self._encoder = "x264"
                        self._frames_written = 0
                    # 새 파일도 0번 슬롯부터 채워(현재 프레임 반복) 소리와 시간축이 어긋나지 않게 한다
                    next_index = 0
                    last = None
                    continue
                self._fail(UserFacingError("encoder", "영상 인코더가 중단되었습니다. 이미 기록된 부분은 복구를 시도합니다.", str(exc)))
                break
