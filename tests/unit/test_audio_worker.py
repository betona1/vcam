"""오디오 작업 스레드: 기본 장치 변경은 장애가 아니라 즉시 다시 여는 사건이다.

2026-11-09 회귀: 모니터 절전으로 NVIDIA 장치가 잠시 사라진 뒤 루프백이 광출력에 남아 3개 녹화가 모두 무음이었다.
"""

import time

from vcam.audio.base import FakeAudioSource
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


def test_real_failure_still_gives_up_after_limit():
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
