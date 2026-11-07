"""순차 다운로드 큐. Qt에 의존하지 않으며, 콜백은 작업 스레드에서 불린다 (UI는 신호로 옮긴다).

항목은 '대기 → 확인 → 내려받는 중 → 완료/실패/취소' 상태를 갖는다. 한 번에 하나만 내려받아
사이트의 속도 제한과 디스크 경합을 피한다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from vcam.download import engine
from vcam.download.engine import DownloadOptions, DownloadResult, MediaCandidate, Progress
from vcam.encoding.ffmpeg import FfmpegPaths

log = logging.getLogger(__name__)

PENDING, PROBING, DOWNLOADING, DONE, FAILED, CANCELLED = "pending", "probing", "downloading", "done", "failed", "cancelled"
STATE_LABELS = {
    PENDING: "대기 중", PROBING: "정보 읽는 중", DOWNLOADING: "내려받는 중",
    DONE: "완료", FAILED: "실패", CANCELLED: "취소됨",
}  # fmt: skip


@dataclass
class QueueItem:
    id: int
    url: str
    options: DownloadOptions
    state: str = PENDING
    candidate: MediaCandidate | None = None
    progress: Progress | None = None
    result: DownloadResult | None = None
    error: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def title(self) -> str:
        return self.candidate.title if self.candidate else self.url

    @property
    def files(self) -> list[Path]:
        return list(self.result.files) if self.result else []

    @property
    def is_active(self) -> bool:
        return self.state in (PENDING, PROBING, DOWNLOADING)


class DownloadQueue:
    """작업 스레드 하나가 항목을 차례로 처리한다. runner를 바꿔 끼워 테스트한다."""

    def __init__(
        self,
        ffmpeg_lookup: Callable[[], FfmpegPaths | None],
        *,
        on_changed: Callable[[QueueItem], None],
        on_log: Callable[[int, str], None] | None = None,
        on_idle: Callable[[], None] | None = None,
        prober: Callable[..., MediaCandidate] | None = None,
        runner: Callable[..., DownloadResult] | None = None,
    ) -> None:
        self._ffmpeg_lookup = ffmpeg_lookup
        self._on_changed = on_changed
        self._on_log = on_log
        self._on_idle = on_idle
        # None이면 호출 시점의 engine.probe/download를 쓴다 (테스트에서 monkeypatch 가능)
        self._prober = prober
        self._runner = runner
        self._items: list[QueueItem] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_id = 1
        self._current: QueueItem | None = None
        self.js_runtime = engine.find_js_runtime()

    # ── 조회 ──────────────────────────────────────────────────────────────
    @property
    def items(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return any(i.is_active for i in self._items)

    def find(self, item_id: int) -> QueueItem | None:
        with self._lock:
            return next((i for i in self._items if i.id == item_id), None)

    # ── 조작 ──────────────────────────────────────────────────────────────
    def add(self, url: str, options: DownloadOptions) -> QueueItem:
        with self._lock:
            item = QueueItem(self._next_id, url, options)
            self._next_id += 1
            self._items.append(item)
        self._on_changed(item)
        self._ensure_thread()
        self._wake.set()
        return item

    def cancel(self, item_id: int) -> None:
        item = self.find(item_id)
        if item is None or not item.is_active:
            return
        item.cancel_event.set()
        if item.state == PENDING:
            item.state = CANCELLED
            self._on_changed(item)

    def cancel_all(self) -> None:
        for item in self.items:
            self.cancel(item.id)

    def remove(self, item_id: int) -> bool:
        """완료·실패·취소된 항목만 목록에서 뺀다."""
        with self._lock:
            item = next((i for i in self._items if i.id == item_id), None)
            if item is None or item.is_active:
                return False
            self._items.remove(item)
        return True

    def retry(self, item_id: int) -> QueueItem | None:
        item = self.find(item_id)
        if item is None or item.is_active:
            return None
        self.remove(item_id)
        return self.add(item.url, item.options)

    def shutdown(self, timeout: float = 10.0) -> None:
        self.cancel_all()
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)

    # ── 작업 스레드 ─────────────────────────────────────────────────────────
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="vcam-download", daemon=True)
            self._thread.start()

    def _take_next(self) -> QueueItem | None:
        with self._lock:
            for item in self._items:
                if item.state == PENDING and not item.cancel_event.is_set():
                    item.state = PROBING
                    self._current = item
                    return item
            self._current = None
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._take_next()
            if item is None:
                if self._on_idle:
                    self._on_idle()
                self._wake.wait()
                self._wake.clear()
                continue
            self._process(item)

    def _log(self, level: int, text: str) -> None:
        if self._on_log:
            self._on_log(level, text)

    def _process(self, item: QueueItem) -> None:
        self._on_changed(item)
        cancel = item.cancel_event.is_set
        try:
            prober = self._prober or engine.probe
            runner = self._runner or engine.download
            item.candidate = prober(item.url, item.options, self.js_runtime)
            if cancel():
                raise engine.Cancelled()
            item.state = DOWNLOADING
            self._on_changed(item)

            def on_progress(p: Progress) -> None:
                item.progress = p
                self._on_changed(item)

            item.result = runner(
                item.url, item.options, self._ffmpeg_lookup(),
                progress=on_progress, log_fn=lambda t: self._log(logging.INFO, f"[{item.title[:40]}] {t}"),
                cancel=cancel, js_runtime=self.js_runtime,
            )  # fmt: skip
            item.state = DONE
            if item.candidate is not None and not item.candidate.duration_s and item.result.duration_s:
                item.candidate = replace(item.candidate, duration_s=item.result.duration_s)
            for err in item.result.errors:
                self._log(logging.WARNING, f"[{item.title[:40]}] {err}")
        except engine.Cancelled:
            item.state = CANCELLED
        except engine.DownloadError as exc:
            item.state = CANCELLED if cancel() else FAILED
            item.error = str(exc)
            self._log(logging.ERROR, f"[{item.title[:40]}] {exc}")
        except Exception as exc:  # noqa: BLE001 - 작업 스레드가 조용히 죽지 않게 한다
            log.error("다운로드 작업 오류", exc_info=True)
            item.state = FAILED
            item.error = f"예기치 않은 오류: {exc}"
            self._log(logging.ERROR, f"[{item.title[:40]}] {item.error}")
        finally:
            self._on_changed(item)
