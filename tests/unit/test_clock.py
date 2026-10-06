from vcam.util.clock import SessionClock, due_frame_index, slot_start_ns


class FakeNow:
    def __init__(self):
        self.t = 1_000

    def __call__(self):
        return self.t


def test_pause_time_is_excluded():
    now = FakeNow()
    clock = SessionClock(now)
    clock.start()
    now.t += 2_000_000_000
    clock.pause()
    now.t += 5_000_000_000
    assert clock.active_ns() == 2_000_000_000
    clock.resume()
    now.t += 1_000_000_000
    assert clock.active_ns() == 3_000_000_000


def test_double_pause_and_resume_are_harmless():
    now = FakeNow()
    clock = SessionClock(now)
    clock.start()
    clock.pause()
    clock.pause()
    now.t += 10
    clock.resume()
    clock.resume()
    assert clock.active_ns() == 0


def test_frame_slots():
    assert due_frame_index(0, 30) == 0
    assert due_frame_index(33_333_333, 30) == 0
    assert due_frame_index(33_333_334, 30) == 1
    assert due_frame_index(1_000_000_000, 30) == 30
    assert slot_start_ns(30, 30) == 1_000_000_000
    assert due_frame_index(slot_start_ns(7, 30), 30) == 7
