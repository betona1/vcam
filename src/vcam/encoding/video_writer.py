"""BGRA 원시 프레임을 FFmpeg stdin으로 보내 Matroska 중간 파일을 만든다."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from vcam.encoding.encoder_probe import encoder_args
from vcam.encoding.ffmpeg import CREATE_NO_WINDOW, FfmpegPaths

log = logging.getLogger(__name__)


class EncoderError(Exception):
    pass


class FfmpegVideoWriter:
    def __init__(
        self,
        ffmpeg: FfmpegPaths,
        width: int,
        height: int,
        fps: int,
        encoder_key: str,
        quality: str,
        out_path: Path,
        log_path: Path,
    ) -> None:
        self.width, self.height, self.fps = width, height, fps
        self.encoder_key = encoder_key
        self._args = [
            str(ffmpeg.ffmpeg), "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}",
            "-framerate", str(fps), "-i", "-",
            # 홀수 크기는 영역을 자르지 않고 최대 1px 검은 패딩으로 맞춘다.
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
            *encoder_args(encoder_key, quality),
            # 클러스터를 1초 단위로 닫고 바로 디스크에 내보내, 비정상 종료 때 잃는 구간을 1초 안팎으로 줄인다.
            "-g", str(fps * 2), "-flush_packets", "1",
            "-f", "matroska", "-cluster_time_limit", "1000", str(out_path),
        ]  # fmt: skip
        self._log_path = log_path
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_file = None

    def start(self) -> None:
        self._log_file = open(self._log_path, "ab")  # noqa: SIM115 - 프로세스 수명 동안 유지
        log.info("FFmpeg 시작: encoder=%s %dx%d@%d", self.encoder_key, self.width, self.height, self.fps)
        try:
            self._proc = subprocess.Popen(
                self._args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._log_file,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self._log_file.close()
            raise EncoderError(f"FFmpeg를 실행하지 못했습니다: {exc}") from exc

    def write(self, frame: np.ndarray) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise EncoderError("인코더가 시작되지 않았습니다")
        try:
            self._proc.stdin.write(memoryview(np.ascontiguousarray(frame)).cast("B"))
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise EncoderError(f"인코더가 프레임을 받지 못했습니다 (코드 {self._proc.poll()})") from exc

    def close(self, timeout: float = 30.0) -> int:
        """stdin을 닫고 정상 종료를 기다린 뒤, 제한 시간을 넘기면 강제 종료한다."""
        proc = self._proc
        if proc is None:
            return -1
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            log.warning("FFmpeg stdin 닫기 실패", exc_info=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.error("FFmpeg가 %.0f초 안에 끝나지 않아 종료합니다", timeout)
            proc.terminate()
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait(timeout=5)
        finally:
            if self._log_file:
                self._log_file.close()
        self._proc = None
        log.info("FFmpeg 종료 코드: %s", code)
        return code
