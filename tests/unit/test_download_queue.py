"""순차 다운로드 큐: 차례 처리, 취소, 실패, 제거/다시 받기 (가짜 prober/runner, Qt·네트워크 불필요)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from vcam.download import engine
from vcam.download.engine import DownloadOptions, DownloadResult, MediaCandidate, Progress
from vcam.download.queue import CANCELLED, DONE, FAILED, PENDING, DownloadQueue


def wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def make_queue(runner, prober=None, **kw):
    events = []
    idle = threading.Event()
    q = DownloadQueue(
        lambda: None,
        on_changed=lambda item: events.append((item.id, item.state)),
        on_log=lambda level, text: events.append(("log", text)),
        on_idle=idle.set,
        prober=prober or (lambda url, o, rt: MediaCandidate(url=url, title="T:" + url, duration_s=3)),
        runner=runner,
        **kw,
    )
    return q, events, idle


def test_items_are_processed_in_order_one_at_a_time(tmp_path):
    active = []
    order = []

    def runner(url, options, ffmpeg, *, progress, log_fn, cancel, js_runtime):
        active.append(url)
        assert len(active) == 1  # 동시에 하나만
        progress(Progress("download", 0.5, 50, 100, 10.0, 5))
        time.sleep(0.05)
        order.append(url)
        active.remove(url)
        f = tmp_path / f"{url[-1]}.mp4"
        f.write_bytes(b"x")
        return DownloadResult(files=[f])

    q, events, idle = make_queue(runner)
    a = q.add("https://x/a", DownloadOptions())
    b = q.add("https://x/b", DownloadOptions())
    assert q.is_busy
    assert wait_until(lambda: a.state == DONE and b.state == DONE)
    assert order == ["https://x/a", "https://x/b"]
    assert a.title == "T:https://x/a" and a.files == [tmp_path / "a.mp4"]
    assert a.progress is not None and a.progress.fraction == 0.5
    assert wait_until(lambda: not q.is_busy and idle.is_set())
    q.shutdown()


def test_cancel_pending_and_running(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def runner(url, options, ffmpeg, *, progress, log_fn, cancel, js_runtime):
        started.set()
        release.wait(5)
        if cancel():
            raise engine.Cancelled()
        return DownloadResult(files=[Path("x.mp4")])

    q, events, _ = make_queue(runner)
    a = q.add("https://x/a", DownloadOptions())
    b = q.add("https://x/b", DownloadOptions())
    assert started.wait(5)
    q.cancel(b.id)  # 대기 중 → 바로 취소
    assert b.state == CANCELLED
    q.cancel(a.id)  # 진행 중 → 취소 요청 후 runner가 멈춤
    release.set()
    assert wait_until(lambda: a.state == CANCELLED)
    assert not q.is_busy
    q.shutdown()


def test_failure_is_recorded_and_queue_continues(tmp_path):
    def runner(url, options, ffmpeg, *, progress, log_fn, cancel, js_runtime):
        if url.endswith("bad"):
            raise engine.DownloadError("영상을 찾을 수 없습니다")
        if url.endswith("boom"):
            raise RuntimeError("kaboom")
        return DownloadResult(files=[Path("ok.mp4")])

    q, events, _ = make_queue(runner)
    bad = q.add("https://x/bad", DownloadOptions())
    boom = q.add("https://x/boom", DownloadOptions())
    ok = q.add("https://x/ok", DownloadOptions())
    assert wait_until(lambda: ok.state == DONE)
    assert bad.state == FAILED and "찾을 수 없습니다" in bad.error
    assert boom.state == FAILED and "예기치 않은" in boom.error
    logs = [t for k, t in events if k == "log"]
    assert any("찾을 수 없습니다" in t for t in logs)
    q.shutdown()


def test_probe_failure_marks_failed():
    def prober(url, o, rt):
        raise engine.DownloadError("지원하지 않는 주소입니다.")

    q, _, _ = make_queue(lambda *a, **k: DownloadResult(), prober=prober)
    item = q.add("https://x/a", DownloadOptions())
    assert wait_until(lambda: item.state == FAILED)
    assert "지원하지 않는" in item.error
    q.shutdown()


def test_remove_only_finished_and_retry_requeues():
    gate = threading.Event()

    def runner(url, options, ffmpeg, *, progress, log_fn, cancel, js_runtime):
        gate.wait(5)
        return DownloadResult(files=[Path("ok.mp4")])

    q, _, _ = make_queue(runner)
    a = q.add("https://x/a", DownloadOptions(quality="720"))
    assert q.remove(a.id) is False  # 진행 중에는 못 뺀다
    gate.set()
    assert wait_until(lambda: a.state == DONE)
    again = q.retry(a.id)
    assert again is not None and again.id != a.id and again.options.quality == "720"
    assert q.find(a.id) is None
    assert wait_until(lambda: again.state == DONE)
    assert q.remove(again.id) is True and q.items == []
    q.shutdown()


def test_cancel_all_before_thread_runs():
    q, _, _ = make_queue(lambda *a, **k: DownloadResult())
    q.cancel_all()  # 비어 있어도 안전
    item = q.add("https://x/a", DownloadOptions())
    assert item.state in (PENDING, DONE) or item.is_active
    q.shutdown()
    assert not q.is_busy
