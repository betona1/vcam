"""PyInstaller 런타임 훅: 배포본에서 PyAV를 대신하는 빈 `av` 모듈.

faster-whisper는 `import av`를 모듈 최상위에서 하지만, vCAM은 소리 추출에 외부 FFmpeg를 쓰므로
PyAV의 함수를 호출하지 않는다. PyAV의 Windows 휠에는 GPL 빌드 FFmpeg DLL(libx264/x265 포함)이
동봉되어 있어, 이를 배포본에 넣으면 MIT 배포본 전체가 GPL 조건을 따라야 한다.
그래서 build.ps1은 `--exclude-module av`로 PyAV를 빼고, 이 훅이 import만 통과하는 대체 모듈을 등록한다.
실수로 PyAV 기능이 호출되면 조용히 실패하지 않고 명확한 오류를 낸다.
"""

import sys
import types


class _NoPyAV(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise RuntimeError(
            f"av.{name}: 이 vCAM 배포본에는 PyAV가 포함되어 있지 않습니다 (라이선스 사유). "
            "소리 추출은 FFmpeg로 처리됩니다."
        )


if "av" not in sys.modules:
    _stub = _NoPyAV("av")
    _stub.__path__ = []  # 패키지처럼 보이게 해 `import av.xxx`도 명확한 오류로 끝나게 한다
    sys.modules["av"] = _stub
