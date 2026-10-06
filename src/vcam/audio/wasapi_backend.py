"""SoundCard(WASAPI) 기반 시스템 소리 루프백과 마이크 캡처.

Qt 메인 스레드는 COM을 STA로 초기화하므로, 오디오 작업은 모두 별도 스레드에서
com_init_mta()를 호출한 뒤 수행한다.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import warnings

import numpy as np

from vcam.audio.base import BLOCK_FRAMES, SAMPLE_RATE, AudioDevice, AudioError, AudioKind

log = logging.getLogger(__name__)

RPC_E_CHANGED_MODE = 0x80010106


def com_init_mta() -> None:
    hr = ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
    if hr < 0 and (hr & 0xFFFFFFFF) != RPC_E_CHANGED_MODE:
        log.warning("COM 초기화 실패: 0x%08X", hr & 0xFFFFFFFF)


_soundcard_module = None
_soundcard_lock = threading.Lock()


def _soundcard():
    """SoundCard는 import 시점 스레드에서 CoInitializeEx를 호출하고, 그 스레드가 이미 COM을
    초기화했다면(S_FALSE) 오류로 처리해 import 자체가 실패한다. 그래서 COM 상태가 없는 새 스레드에서
    한 번만 import한다."""
    global _soundcard_module
    with _soundcard_lock:
        if _soundcard_module is None:
            box: dict = {}

            def load() -> None:
                try:
                    import soundcard

                    box["module"] = soundcard
                except Exception as exc:  # noqa: BLE001 - 원인을 그대로 전달
                    box["error"] = exc

            loader = threading.Thread(target=load, name="vcam-soundcard-import")
            loader.start()
            loader.join()
            if "error" in box:
                raise AudioError(f"오디오 라이브러리를 불러오지 못했습니다: {box['error']}")
            _soundcard_module = box["module"]
            warnings.filterwarnings("ignore", category=_soundcard_module.SoundcardRuntimeWarning)
    return _soundcard_module


def _safe_default(getter):
    """기본 장치가 없으면 SoundCard가 E_NOTFOUND(0x80070490)를 예외로 던진다."""
    try:
        return getter()
    except RuntimeError:
        return None


def list_devices() -> tuple[list[AudioDevice], list[AudioDevice]]:
    """(출력 장치 = 시스템 소리 루프백 대상, 마이크). 작업 스레드에서 호출한다."""
    sc = _soundcard()
    com_init_mta()
    default_speaker = _safe_default(sc.default_speaker)
    default_mic = _safe_default(sc.default_microphone)
    speakers = [
        AudioDevice(s.id, s.name, "system", default_speaker is not None and s.id == default_speaker.id)
        for s in sc.all_speakers()
    ]
    mics = [
        AudioDevice(m.id, m.name, "microphone", default_mic is not None and m.id == default_mic.id)
        for m in sc.all_microphones(include_loopback=False)
    ]
    return speakers, mics


class WasapiSource:
    def __init__(self, kind: AudioKind, device_id: str = "") -> None:
        self.kind = kind
        self.device_id = device_id
        self.samplerate = SAMPLE_RATE
        self.channels = 2 if kind == "system" else 1
        self.device_name = ""
        self._ctx = None
        self._recorder = None

    def open(self) -> None:
        sc = _soundcard()
        com_init_mta()
        try:
            if self.kind == "system":
                speaker = sc.get_speaker(self.device_id) if self.device_id else _safe_default(sc.default_speaker)
                if speaker is None:
                    raise AudioError("출력 장치가 없습니다")
                device = sc.get_microphone(speaker.id, include_loopback=True)
                self.device_name = speaker.name
            else:
                device = sc.get_microphone(self.device_id) if self.device_id else _safe_default(sc.default_microphone)
                if device is None:
                    raise AudioError("연결된 마이크가 없습니다")
                self.device_name = device.name
            self._ctx = device.recorder(samplerate=self.samplerate, channels=self.channels, blocksize=BLOCK_FRAMES)
            self._recorder = self._ctx.__enter__()
        except AudioError:
            raise
        except Exception as exc:  # noqa: BLE001 - COM/WASAPI 오류 종류가 다양함
            raise AudioError(f"오디오 장치를 열지 못했습니다: {exc}") from exc
        log.info("오디오 장치 열림: %s (%s, %dch)", self.device_name, self.kind, self.channels)

    def read(self) -> np.ndarray:
        if self._recorder is None:
            raise AudioError("오디오 장치가 열려 있지 않습니다")
        try:
            data = self._recorder.record(numframes=BLOCK_FRAMES)
        except Exception as exc:  # noqa: BLE001 - 장치 분리 시 COM 오류
            raise AudioError(f"오디오 장치 오류: {exc}") from exc
        return np.asarray(data, dtype=np.float32).reshape(-1, self.channels)

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - 이미 분리된 장치 해제 실패는 무시해도 안전
                log.debug("오디오 장치 해제 중 오류", exc_info=True)
        self._ctx = None
        self._recorder = None
