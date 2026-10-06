"""녹화 상태 머신.

CLAUDE.md §8의 단일 상태 머신을 그대로 구현한다. 한 가지 확장: READY에서 다른
대상을 고를 수 있도록 READY --select_source--> SELECTING 전이를 허용한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

log = logging.getLogger(__name__)


class RecordingState(StrEnum):
    IDLE = "idle"
    SELECTING = "selecting"
    READY = "ready"
    COUNTDOWN = "countdown"
    RECORDING = "recording"
    PAUSED = "paused"
    FINALIZING = "finalizing"
    RECOVERING = "recovering"
    REVIEW = "review"
    ERROR = "error"


class Command(StrEnum):
    SELECT_SOURCE = "select_source"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    START = "start"
    ELAPSED = "elapsed"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    FATAL_ERROR = "fatal_error"
    SUCCESS = "success"
    FAILURE = "failure"
    RECOVERED = "recovered"
    FAILED = "failed"
    NEW_RECORDING = "new_recording"
    CLOSE = "close"
    ACKNOWLEDGE = "acknowledge"


S = RecordingState
C = Command

TRANSITIONS: dict[RecordingState, dict[Command, RecordingState]] = {
    S.IDLE: {C.SELECT_SOURCE: S.SELECTING},
    S.SELECTING: {C.CONFIRM: S.READY, C.CANCEL: S.IDLE},
    S.READY: {C.START: S.COUNTDOWN, C.SELECT_SOURCE: S.SELECTING},
    S.COUNTDOWN: {C.ELAPSED: S.RECORDING, C.CANCEL: S.READY},
    S.RECORDING: {C.PAUSE: S.PAUSED, C.STOP: S.FINALIZING, C.FATAL_ERROR: S.RECOVERING},
    S.PAUSED: {C.RESUME: S.RECORDING, C.STOP: S.FINALIZING, C.FATAL_ERROR: S.RECOVERING},
    S.FINALIZING: {C.SUCCESS: S.REVIEW, C.FAILURE: S.RECOVERING},
    S.RECOVERING: {C.RECOVERED: S.REVIEW, C.FAILED: S.ERROR},
    S.REVIEW: {C.NEW_RECORDING: S.READY, C.CLOSE: S.IDLE},
    S.ERROR: {C.ACKNOWLEDGE: S.READY},
}

# 녹화가 진행 중이거나 마무리 중이어서 대상·설정을 바꾸면 안 되는 상태
BUSY_STATES = frozenset({S.COUNTDOWN, S.RECORDING, S.PAUSED, S.FINALIZING, S.RECOVERING})


class InvalidTransition(Exception):
    def __init__(self, state: RecordingState, command: Command) -> None:
        super().__init__(f"'{state}' 상태에서는 '{command}' 명령을 처리할 수 없습니다")
        self.state = state
        self.command = command


class StateMachine:
    def __init__(self, initial: RecordingState = S.IDLE) -> None:
        self._state = initial
        self._listeners: list[Callable[[RecordingState, RecordingState, Command], None]] = []

    @property
    def state(self) -> RecordingState:
        return self._state

    def can(self, command: Command) -> bool:
        return command in TRANSITIONS[self._state]

    def add_listener(self, fn: Callable[[RecordingState, RecordingState, Command], None]) -> None:
        self._listeners.append(fn)

    def fire(self, command: Command) -> RecordingState:
        target = TRANSITIONS[self._state].get(command)
        if target is None:
            log.warning("잘못된 전이 요청: state=%s command=%s", self._state, command)
            raise InvalidTransition(self._state, command)
        previous, self._state = self._state, target
        log.info("상태 전이: %s --%s--> %s", previous, command, target)
        for fn in self._listeners:
            fn(previous, target, command)
        return target
