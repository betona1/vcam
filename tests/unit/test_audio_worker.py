"""오디오 작업 스레드: 기본 장치 변경은 장애가 아니라 즉시 다시 여는 사건이다.

2026-11-09 회귀: 모니터 절전으로 NVIDIA 장치가 잠시 사라진 뒤 루프백이 광출력에 남아 3개 녹화가 모두 무음이었다.
"""

import time

from vcam.audio import worker as worker_mod
from vcam.audio.base import SAMPLE_RATE, AudioError, FakeAudioSource
from vcam.audio.worker import MAX_REOPEN_ATTEMPTS, AudioWorker, WorkerStatus


def test_default_device_change_reopens_without_counting_as_failure():
    opened: list[str] = []

    def factory():
        name = f"장치 {len(opened)}"
        opened.append(name)
        # 매번 2블록(40ms) 뒤에 기본 장치가 바뀌었다고 알린다 → 재시도 한도보다 훨씬 많이 다시 연다
        src = FakeAudioSource(channels=2, switch_after_blocks=2)
        src.device_name = name
        return src

    worker = AudioWorker("system", factory)
    worker.start()
    try:
        assert worker.wait_opened(2.0)
        deadline = time.monotonic() + 5.0
        while worker.device_changes < MAX_REOPEN_ATTEMPTS + 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker.device_changes >= MAX_REOPEN_ATTEMPTS + 2, "장치 변경은 재시도 횟수에 세지 않아야 한다"
        assert worker.status is WorkerStatus.OK
        assert worker.device_name == opened[-1]
        assert len(opened) == worker.device_changes + 1
    finally:
        worker.stop()


def test_real_failure_is_reported_after_limit():
    def factory():
        return FakeAudioSource(channels=2, fail_after_blocks=1)

    worker = AudioWorker("system", factory)
    worker.start()
    try:
        assert worker.wait_opened(2.0)
        deadline = time.monotonic() + 15.0
        while worker.status is not WorkerStatus.FAILED and time.monotonic() < deadline:
            time.sleep(0.05)
        assert worker.status is WorkerStatus.FAILED
    finally:
        worker.stop()


class _MissingThenBack(FakeAudioSource):
    """처음 `missing_opens`번은 장치가 없다(모니터 절전으로 HDMI 소리 장치가 사라진 상황)."""

    opens = 0

    def __init__(self, missing_opens: int) -> None:
        super().__init__(channels=2)
        self._missing = missing_opens

    def open(self) -> None:
        type(self).opens += 1
        if type(self).opens <= self._missing:
            raise AudioError("출력 장치가 없습니다")
        super().open()


def test_lost_device_is_watched_and_reopened_when_it_returns(monkeypatch):
    """2026-11-12 회귀: 재시도 5번 뒤 포기해 장치가 돌아와도 다음 녹화가 소리 없이 저장됐다."""
    monkeypatch.setattr(worker_mod, "REOPEN_DELAY_S", 0.01)
    monkeypatch.setattr(worker_mod, "WATCH_INTERVAL_S", 0.05)
    _MissingThenBack.opens = 0
    worker = AudioWorker("system", lambda: _MissingThenBack(MAX_REOPEN_ATTEMPTS + 3))
    worker.start()
    try:
        assert not worker.wait_opened(2.0)
        assert worker.status is WorkerStatus.FAILED
        assert (worker.samplerate, worker.channels) == (SAMPLE_RATE, 2), "장치가 없어도 형식은 알고 있어야 한다"
        deadline = time.monotonic() + 5.0
        while worker.status is not WorkerStatus.OK and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker.status is WorkerStatus.OK, "장치가 돌아오면 다시 열어야 한다"
    finally:
        worker.stop()
