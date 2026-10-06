import pytest

from vcam.domain.states import TRANSITIONS, Command, InvalidTransition, RecordingState, StateMachine

S, C = RecordingState, Command


def test_happy_path_to_review():
    m = StateMachine()
    for cmd, expected in [
        (C.SELECT_SOURCE, S.SELECTING), (C.CONFIRM, S.READY), (C.START, S.COUNTDOWN),
        (C.ELAPSED, S.RECORDING), (C.PAUSE, S.PAUSED), (C.RESUME, S.RECORDING),
        (C.STOP, S.FINALIZING), (C.SUCCESS, S.REVIEW), (C.NEW_RECORDING, S.READY),
    ]:  # fmt: skip
        assert m.fire(cmd) is expected


def test_failure_path_to_error_and_back():
    m = StateMachine(S.RECORDING)
    assert m.fire(C.FATAL_ERROR) is S.RECOVERING
    assert m.fire(C.FAILED) is S.ERROR
    assert m.fire(C.ACKNOWLEDGE) is S.READY


@pytest.mark.parametrize("state", list(S))
def test_every_undefined_command_is_rejected(state):
    for cmd in C:
        m = StateMachine(state)
        if cmd in TRANSITIONS[state]:
            assert m.fire(cmd) is TRANSITIONS[state][cmd]
        else:
            with pytest.raises(InvalidTransition):
                m.fire(cmd)
            assert m.state is state


def test_listener_receives_transition():
    seen = []
    m = StateMachine()
    m.add_listener(lambda prev, new, cmd: seen.append((prev, new, cmd)))
    m.fire(C.SELECT_SOURCE)
    assert seen == [(S.IDLE, S.SELECTING, C.SELECT_SOURCE)]
