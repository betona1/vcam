"""실제 화면을 3초 녹화하는 스모크 테스트(GDI와 DXcam, 자동 인코더)."""

import time

import pytest

from vcam.capture.base import CaptureError
from vcam.capture.factory import open_capture_backend
from vcam.capture.region import list_monitors
from vcam.domain.models import Rect, SessionPaths
from vcam.encoding.encoder_probe import select_encoder
from vcam.encoding.muxer import finalize_to_output
from vcam.services.recording_pipeline import PipelineConfig, RecordingPipeline

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("backend", ["mss", "dxcam"])
def test_real_three_second_recording(tmp_path, ffmpeg, backend):
    primary = next(m for m in list_monitors() if m.is_primary)
    rect = Rect(primary.rect.left + 100, primary.rect.top + 100, 801, 451)
    if backend == "dxcam":
        try:
            open_capture_backend("dxcam", rect).close()
        except CaptureError as exc:
            pytest.skip(f"DXcam 사용 불가: {exc}")

    encoder = select_encoder(ffmpeg).key
    paths = SessionPaths.in_dir(tmp_path / "session")
    paths.directory.mkdir()
    config = PipelineConfig(rect, 30, encoder, "balanced", ffmpeg, paths.video_partial, paths.ffmpeg_log)
    used = []

    def opener(r):
        b = open_capture_backend(backend, r)
        used.append(b.name)
        return b

    errors = []
    pipeline = RecordingPipeline(config, opener, errors.append)
    pipeline.start()
    assert pipeline.wait_ready(10)
    time.sleep(3.0)
    outcome = pipeline.stop()
    assert not errors, errors
    assert outcome.error is None
    info = finalize_to_output(ffmpeg, paths.video_partial, tmp_path / "out", f"smoke_{backend}")
    assert (info.width, info.height) == (802, 452)
    assert 2.7 <= info.duration_s <= 3.4
    assert info.codec == "h264"
    print(f"\nbackend={used} encoder={outcome.encoder} frames={outcome.frames_written} dropped={outcome.frames_dropped}")
