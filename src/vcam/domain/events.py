"""스레드 경계를 넘는 타입이 명확한 이벤트 객체."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserFacingError:
    """사용자에게 보여줄 오류. message는 무엇을 하면 되는지까지 한국어로 담는다."""

    code: str
    message: str
    detail: str = ""


class VcamError(Exception):
    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.error = UserFacingError(code, message, detail)
