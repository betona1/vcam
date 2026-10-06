"""UI와 녹화 엔진 사이의 유일한 통로. GUI 스레드에 살며 상태 머신을 소유한다.

무거운 작업(인코더 선택, 오디오 장치 대기, 파이프라인 종료, 믹스, 검증)은 모두 작업 스레드에서
수행하고 결과는 Qt 신호로 GUI 스레드에 돌려준다. 영상과 오디오는 하나의 SessionClock을 공유한다.
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from vcam.audio.track_writer import AudioTrack
from vcam.audio.worker import WorkerStatus
from vcam.capture.factory import open_capture_backend
from vcam.domain.events import UserFacingError
from vcam.domain.models import (
    CaptureSource,
    MediaInfo,
    RecordingMetrics,
    RecordingResult,
    SessionPaths,
)
from vcam.domain.states import BUSY_STATES, Command, InvalidTransition, RecordingState, StateMachine
from vcam.encoding.encoder_probe import select_encoder
from vcam.encoding.ffmpeg import FfmpegPaths, find_ffmpeg
from vcam.encoding.muxer import MuxError, finalize_with_fallback
from vcam.encoding.validator import ValidationError
from vcam.encoding.video_writer import EncoderError
from vcam.services.audio_service import TRACK_LABELS, AudioConfig, AudioService
from vcam.services.profile_service import Settings
from vcam.services.recording_pipeline import PipelineConfig, PipelineOutcome, RecordingPipeline
from vcam.services.recovery_service import write_session_meta
from vcam.util.clock import SessionClock
from vcam.util.paths import (
    free_bytes,
    is_writable_dir,
    new_session_id,
    render_filename,
    sessions_dir,
)

log = logging.getLogger(__name__)

MIN_START_FREE_BYTES = 1024**3


class _Bridge(QObject):
    """작업 스레드 → GUI 스레드 전달용. 신호는 수신 객체 스레드에서 실행된다."""

    prepared = Signal(object)
    finished = Signal(object)
    fatal = Signal(object)


class RecordingController(QObject):
    state_changed = Signal(object)  # RecordingState
    countdown_tick = Signal(int)
    metrics_changed = Signal(object)  # RecordingMetrics
    recording_saved = Signal(object)  # RecordingResult
    error_raised = Signal(object)  # UserFacingError
    notice = Signal(str)

    def __init__(self, settings: Settings, parent: QObject | None = None, audio: AudioService | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.audio = audio or AudioService()
        self._tracks: list[AudioTrack] = []
        self._audio_status: dict[str, WorkerStatus | None] = {}
        self._finalize_thread: threading.Thread | None = None
        self._prepare_gen = 0
        self.machine = StateMachine()
        self.machine.add_listener(lambda _p, new, _c: self.state_changed.emit(new))
        self.source: CaptureSource | None = None
        self._pipeline: RecordingPipeline | None = None
        self._paths: SessionPaths | None = None
        self._prepared: dict | None = None
        self._countdown_left = 0
        self._stem = ""

        self._bridge = _Bridge(self)
        self._bridge.prepared.connect(self._on_prepared)
        self._bridge.finished.connect(self._on_finished)
        self._bridge.fatal.connect(self._on_fatal)

        self._countdown_timer = QTimer(self, interval=1000)
        self._countdown_timer.timeout.connect(self._on_countdown)
        self._metrics_timer = QTimer(self, interval=250)
        self._metrics_timer.timeout.connect(self._emit_metrics)
        self._audio_timer = QTimer(self, interval=1000)
        self._audio_timer.timeout.connect(self._check_audio_status)
        self._audio_timer.start()
        self.apply_audio_settings()

    # ── 조회 ────────────────────────────────────────────────────────────────
    @property
    def state(self) -> RecordingState:
        return self.machine.state

    @property
    def is_busy(self) -> bool:
        return self.state in BUSY_STATES

    @property
    def current_session_dir(self) -> Path | None:
        """녹화 중이거나 준비 중인 세션 폴더(복구 검사에서 제외해야 한다)."""
        if self._paths:
            return self._paths.directory
        return self._prepared["dir"] if self._prepared else None

    def update_settings(self, settings: Settings) -> None:
        self.settings = settings
        if not self.is_busy:
            self.apply_audio_settings()

    def audio_config(self) -> AudioConfig:
        p, s = self.settings.profile, self.settings
        return AudioConfig(p.system_audio_enabled, s.system_audio_device, p.microphone_enabled, s.mic_device)

    def apply_audio_settings(self) -> None:
        self.audio.configure(self.audio_config())

    def set_mic_muted(self, muted: bool) -> None:
        self.audio.set_mic_muted(muted)

    @Slot()
    def _check_audio_status(self) -> None:
        """장치 분리·재연결을 사용자에게 알린다."""
        for kind, label in TRACK_LABELS.items():
            status = self.audio.status(kind)
            previous = self._audio_status.get(kind)
            self._audio_status[kind] = status
            if status is previous or status is None:
                continue
            if status is WorkerStatus.RECONNECTING:
                self.notice.emit(f"{label} 장치 연결이 끊겨 다시 연결하는 중입니다. 끊긴 구간은 무음으로 채웁니다.")
            elif status is WorkerStatus.FAILED:
                worker = self.audio.worker(kind)
                self.notice.emit(f"{label} 장치를 사용할 수 없습니다: {worker.error if worker else ''}")
            elif status is WorkerStatus.OK and previous is WorkerStatus.RECONNECTING:
                self.notice.emit(f"{label} 장치에 다시 연결했습니다.")

    # ── 명령 ────────────────────────────────────────────────────────────────
    def _fire(self, command: Command) -> bool:
        try:
            self.machine.fire(command)
            return True
        except InvalidTransition as exc:
            log.warning("무시된 명령: %s", exc)
            return False

    def begin_selection(self) -> bool:
        if self.state in (RecordingState.REVIEW,):
            self._fire(Command.NEW_RECORDING)
        if self.state is RecordingState.ERROR:
            self._fire(Command.ACKNOWLEDGE)
        return self.state is RecordingState.SELECTING or self._fire(Command.SELECT_SOURCE)

    def cancel_selection(self) -> None:
        if self.state is RecordingState.SELECTING:
            self._fire(Command.CONFIRM if self.source else Command.CANCEL)

    def set_source(self, source: CaptureSource) -> None:
        if self.is_busy:
            self.notice.emit("녹화 중에는 대상을 바꿀 수 없습니다.")
            return
        if self.state is not RecordingState.SELECTING and not self.begin_selection():
            return
        self.source = source
        self._fire(Command.CONFIRM)

    @Slot()
    def toggle_start_stop(self) -> None:
        if self.state in (RecordingState.RECORDING, RecordingState.PAUSED):
            self.stop()
        elif self.state is RecordingState.COUNTDOWN:
            self.cancel_countdown()
        else:
            self.start()

    @Slot()
    def toggle_pause(self) -> None:
        if self.state is RecordingState.RECORDING:
            self.pause()
        elif self.state is RecordingState.PAUSED:
            self.resume()

    def start(self) -> None:
        if self.state is RecordingState.REVIEW:
            self._fire(Command.NEW_RECORDING)
        if self.state is RecordingState.ERROR:
            self._fire(Command.ACKNOWLEDGE)
        if self.state is not RecordingState.READY or self.source is None:
            self.notice.emit("먼저 녹화할 화면이나 영역을 선택해 주세요.")
            return
        error = self._preflight()
        if error:
            self.error_raised.emit(error)
            return
        self._prepared = None
        self._fire(Command.START)
        self._countdown_left = max(0, int(self.settings.countdown_s))
        self._prepare_gen += 1
        threading.Thread(target=self._prepare, args=(self._prepare_gen,), name="vcam-prepare", daemon=True).start()
        if self._countdown_left > 0:
            self.countdown_tick.emit(self._countdown_left)
            self._countdown_timer.start()

    def cancel_countdown(self) -> None:
        if self.state is RecordingState.COUNTDOWN:
            self._countdown_timer.stop()
            self._fire(Command.CANCEL)
            self._cleanup_unused_session()

    def pause(self) -> None:
        if self._pipeline and self._fire(Command.PAUSE):
            self._pipeline.pause()

    def resume(self) -> None:
        if self._pipeline and self._fire(Command.RESUME):
            self._pipeline.resume()

    def stop(self) -> None:
        if self._pipeline is None or not self._fire(Command.STOP):
            return
        self._metrics_timer.stop()
        self._start_finalize(self._pipeline, recovering=False)

    def _start_finalize(self, pipeline: RecordingPipeline, recovering: bool) -> None:
        self._finalize_thread = threading.Thread(target=self._finalize_safe, args=(pipeline, recovering),
                                                 name="vcam-recover" if recovering else "vcam-finalize", daemon=True)  # fmt: skip
        self._finalize_thread.start()

    # ── 준비 ────────────────────────────────────────────────────────────────
    def _preflight(self) -> UserFacingError | None:
        out = Path(self.settings.output_dir)
        if find_ffmpeg(self.settings.ffmpeg_path) is None:
            return UserFacingError(
                "ffmpeg_missing",
                "FFmpeg를 찾을 수 없어 녹화할 수 없습니다. [도구 → 시스템 진단]에서 FFmpeg 위치를 지정해 주세요.",
            )
        if not is_writable_dir(out):
            return UserFacingError("output_not_writable", "저장 폴더에 쓸 수 없습니다. 다른 저장 폴더를 선택해 주세요.")
        if free_bytes(out) < MIN_START_FREE_BYTES:
            return UserFacingError("disk_low", "저장 폴더의 여유 공간이 1GB 미만입니다. 공간을 확보한 뒤 녹화해 주세요.")
        if free_bytes(sessions_dir()) < MIN_START_FREE_BYTES:
            return UserFacingError("disk_low", "녹화 임시 파일을 저장할 C 드라이브(%LOCALAPPDATA%)의 여유 공간이 1GB 미만입니다.")
        return None

    def _prepare(self, gen: int) -> None:
        """작업 스레드: 인코더 선택과 세션 폴더 준비."""
        try:
            ffmpeg = find_ffmpeg(self.settings.ffmpeg_path)
            if ffmpeg is None:
                raise RuntimeError("FFmpeg 없음")
            selection = select_encoder(ffmpeg, self.settings.profile.encoder_preference)
            if not selection.key:
                self._bridge.prepared.emit(UserFacingError(
                    "no_encoder", "사용할 수 있는 H.264 인코더가 없습니다. [도구 → 시스템 진단]을 확인해 주세요."))  # fmt: skip
                return
            audio_failed = self.audio.wait_ready(timeout=3.0)
            session_dir = sessions_dir() / new_session_id()
            session_dir.mkdir(parents=True)
            self._bridge.prepared.emit(
                {"ffmpeg": ffmpeg, "encoder": selection.key, "dir": session_dir, "audio_failed": audio_failed, "gen": gen}
            )
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위
            log.exception("녹화 준비 실패")
            self._bridge.prepared.emit(UserFacingError("prepare", "녹화를 준비하지 못했습니다.", repr(exc)))

    @Slot(object)
    def _on_prepared(self, result: object) -> None:
        stale = isinstance(result, dict) and result.get("gen") != self._prepare_gen
        if self.state is not RecordingState.COUNTDOWN or stale:
            if isinstance(result, dict):
                shutil.rmtree(result["dir"], ignore_errors=True)
            return
        if isinstance(result, UserFacingError):
            self._countdown_timer.stop()
            self._fire(Command.CANCEL)
            self.error_raised.emit(result)
            return
        self._prepared = result
        if self._countdown_left <= 0:
            self._begin_recording()

    @Slot()
    def _on_countdown(self) -> None:
        self._countdown_left -= 1
        if self._countdown_left > 0:
            self.countdown_tick.emit(self._countdown_left)
            return
        self._countdown_timer.stop()
        self.countdown_tick.emit(0)
        if self._prepared is not None:
            self._begin_recording()
        # 준비가 늦으면 _on_prepared에서 시작한다.

    def _begin_recording(self) -> None:
        try:
            self._begin_recording_inner()
        except Exception as exc:  # noqa: BLE001 - 슬롯 안: 상태가 COUNTDOWN에 멈추지 않게 정리
            log.exception("녹화 시작 실패")
            self._countdown_timer.stop()
            self.audio.abort_recording()
            self._pipeline = None
            if self._paths:
                shutil.rmtree(self._paths.directory, ignore_errors=True)
            self._paths = None
            self._prepared = None
            if self.state is RecordingState.COUNTDOWN:
                self._fire(Command.CANCEL)
            self.error_raised.emit(UserFacingError("start_failed", "녹화를 시작하지 못했습니다. 다시 시도해 주세요.", repr(exc)))

    def _begin_recording_inner(self) -> None:
        prepared, source = self._prepared, self.source
        assert prepared is not None and source is not None
        paths = SessionPaths.in_dir(prepared["dir"])
        self._paths = paths
        self._stem = render_filename(self.settings.filename_template, datetime.now())
        profile = self.settings.profile
        clock = SessionClock()
        self._tracks = self.audio.begin_recording(clock, paths.directory)
        for problem in prepared.get("audio_failed", []):
            self.notice.emit(f"소리 없이 녹화합니다 — {problem}")
        write_session_meta(paths, {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "output_stem": self._stem,
            "output_dir": self.settings.output_dir,
            "source": {"kind": source.kind, "rect": list(source.rect.as_ltrb()), "label": source.label},
            "fps": profile.fps,
            "quality": profile.quality_preset,
            "encoder": prepared["encoder"],
            "audio": [t.to_meta() for t in self._tracks],
        })  # fmt: skip
        config = PipelineConfig(
            rect=source.rect,
            fps=profile.fps,
            encoder_key=prepared["encoder"],
            quality=profile.quality_preset,
            ffmpeg=prepared["ffmpeg"],
            partial_path=paths.video_partial,
            ffmpeg_log=paths.ffmpeg_log,
        )
        backend_pref = self.settings.capture_backend
        self._pipeline = RecordingPipeline(
            config,
            open_backend=lambda rect: open_capture_backend(backend_pref, rect),
            on_fatal=self._bridge.fatal.emit,
            clock=clock,
        )
        try:
            self._pipeline.start()  # 스레드와 FFmpeg 프로세스만 띄우고 곧바로 돌아온다
        except EncoderError as exc:
            self._pipeline = None
            self.audio.abort_recording()
            self._fire(Command.CANCEL)
            shutil.rmtree(paths.directory, ignore_errors=True)
            self.error_raised.emit(UserFacingError("encoder", "영상 인코더를 시작하지 못했습니다.", str(exc)))
            return
        self._fire(Command.ELAPSED)
        self._metrics_timer.start()

    # ── 마무리 ──────────────────────────────────────────────────────────────
    @Slot(object)
    def _on_fatal(self, error: UserFacingError) -> None:
        if self.state not in (RecordingState.RECORDING, RecordingState.PAUSED) or self._pipeline is None:
            return
        self._metrics_timer.stop()
        self._fire(Command.FATAL_ERROR)
        self._start_finalize(self._pipeline, recovering=True)
        self.error_raised.emit(error)

    def _finalize_safe(self, pipeline: RecordingPipeline, recovering: bool) -> None:
        """어떤 예외가 나도 finished 신호를 보내 UI가 '저장 중'에 멈추지 않게 한다."""
        try:
            self._finalize(pipeline, recovering)
        except Exception as exc:  # noqa: BLE001 - 스레드 최상위
            log.exception("녹화 마무리 실패")
            self._bridge.finished.emit({
                "outcome": PipelineOutcome(0, 0, pipeline.config.encoder_key, -1, None, ()), "recovering": True, "notes": [],
                "error": UserFacingError("save_failed", "녹화를 마무리하지 못했습니다. 원본 조각은 [도구 → 미완료 녹화 복구]에서 "
                                         "다시 시도할 수 있습니다.", repr(exc)),
            })  # fmt: skip

    def _finalize(self, pipeline: RecordingPipeline, recovering: bool) -> None:
        """작업 스레드: 파이프라인 종료 → MP4 저장. 실패하면 복구를 한 번 더 시도한다."""
        assert self._paths is not None
        paths, stem = self._paths, self._stem
        output_dir = Path(self.settings.output_dir)
        outcome: PipelineOutcome = pipeline.stop()
        result: dict = {"outcome": outcome, "recovering": recovering or outcome.error is not None, "notes": []}
        ffmpeg: FfmpegPaths = pipeline.config.ffmpeg
        tracks = self._tracks
        if outcome.frames_written < max(1, int(pipeline.config.fps * 0.3)):
            self.audio.abort_recording()
            # 오류로 멈춘 경우에는 이미 원인을 알렸으므로 두 번째 오류 창은 띄우지 않는다
            result["silent"] = outcome.error is not None
            result["error"] = UserFacingError("empty", "녹화가 너무 짧아 저장할 영상이 없습니다.")
            shutil.rmtree(paths.directory, ignore_errors=True)
            self._bridge.finished.emit(result)
            return
        # 오디오를 영상 길이(프레임 수 / fps)에 정확히 맞춘다.
        self.audio.end_recording(outcome.frames_written / pipeline.config.fps)
        try:
            result["media"], result["notes"] = finalize_with_fallback(ffmpeg, paths.video_partial, output_dir, stem, tracks)
        except (MuxError, ValidationError, OSError) as exc:
            log.warning("정상 저장 실패, 복구 모드로 재시도: %s", exc)
            result["recovering"] = True
            try:
                result["media"], result["notes"] = finalize_with_fallback(
                    ffmpeg, paths.video_partial, output_dir, stem + "_복구", tracks, tolerant=True
                )
            except (MuxError, ValidationError, OSError) as exc2:
                log.error("복구 실패: %s", exc2)
                result["error"] = UserFacingError(
                    "save_failed",
                    "녹화 파일을 저장하지 못했습니다. 원본 조각은 보존했으니 [도구 → 미완료 녹화 복구]에서 다시 시도할 수 있습니다.",
                    str(exc2),
                )
                self._bridge.finished.emit(result)
                return
        shutil.rmtree(paths.directory, ignore_errors=True)
        self._bridge.finished.emit(result)

    @Slot(object)
    def _on_finished(self, result: dict) -> None:
        outcome: PipelineOutcome = result["outcome"]
        media: MediaInfo | None = result.get("media")
        recovering = result["recovering"]
        self._pipeline = None
        self._paths = None
        self._finalize_thread = None
        self.apply_audio_settings()  # 녹화 중 바꾼 오디오 장치 설정을 이제 반영
        if self.state is RecordingState.FINALIZING:
            self._fire(Command.FAILURE if (recovering or media is None) else Command.SUCCESS)
        if media is None:
            self._fire(Command.FAILED)
            if not result.get("silent"):
                self.error_raised.emit(result.get("error") or UserFacingError("save_failed", "녹화를 저장하지 못했습니다."))
            return
        if self.state is RecordingState.RECOVERING:
            self._fire(Command.RECOVERED)
        self.recording_saved.emit(RecordingResult(
            path=media.path, media=media, encoder=outcome.encoder,
            frames_dropped=outcome.frames_dropped, recovered=recovering,
            notes=(*outcome.notes, *result.get("notes", [])),
        ))  # fmt: skip

    def _cleanup_unused_session(self) -> None:
        if self._prepared:
            shutil.rmtree(self._prepared["dir"], ignore_errors=True)
            self._prepared = None

    @Slot()
    def _emit_metrics(self) -> None:
        if self._pipeline is None:
            return
        metrics = self._pipeline.metrics()
        try:
            size = self._pipeline.config.partial_path.stat().st_size
        except OSError:
            size = 0
        self.metrics_changed.emit(RecordingMetrics(
            elapsed_ns=metrics.elapsed_ns, frames_written=metrics.frames_written,
            frames_dropped=metrics.frames_dropped, measured_fps=metrics.measured_fps,
            file_bytes=size, encoder=metrics.encoder,
        ))  # fmt: skip

    def shutdown(self, timeout: float = 30.0) -> None:
        """앱 종료 시 녹화 중이면 안전하게 마무리한다(GUI 스레드에서 블로킹)."""
        self._countdown_timer.stop()
        self._metrics_timer.stop()
        if self._finalize_thread is not None and self._finalize_thread.is_alive():
            # 이미 저장 중이면 두 번 저장하지 않고 끝나기를 기다린다
            log.info("앱 종료: 진행 중인 저장이 끝나기를 기다립니다")
            self._finalize_thread.join(timeout=max(timeout, 120))
        elif self._pipeline is not None and self._paths is not None:
            log.info("앱 종료: 진행 중인 녹화를 마무리합니다")
            pipeline, paths = self._pipeline, self._paths
            self._pipeline = None
            try:
                outcome = pipeline.stop(timeout=timeout)
                self.audio.end_recording(outcome.frames_written / pipeline.config.fps)
                finalize_with_fallback(
                    pipeline.config.ffmpeg, paths.video_partial, Path(self.settings.output_dir), self._stem, self._tracks
                )
                shutil.rmtree(paths.directory, ignore_errors=True)
            except Exception:  # noqa: BLE001 - 종료 중: 조각은 남겨 다음 실행 때 복구
                log.exception("종료 중 저장 실패 — 다음 실행 때 복구 안내")
        self._cleanup_unused_session()
        self._audio_timer.stop()
        self.audio.shutdown()
