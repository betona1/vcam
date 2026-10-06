"""가짜 캡처 스트림을 실제 FFmpeg로 인코딩하고 ffprobe로 검증한다."""

import time

import pytest

from vcam.capture.base import FakeCaptureBackend
from vcam.domain.models import Rect, SessionPaths
from vcam.encoding.encoder_probe import select_encoder
from vcam.encoding.muxer import finalize_to_output
from vcam.encoding.validator import probe_media
from vcam.services.recording_pipeline import PipelineConfig, RecordingPipeline

pytestmark = pytest.mark.integration


def make_pipeline(tmp_path, ffmpeg, rect, fps=30, encoder="x264", backend=None, errors=None):
    paths = SessionPaths.in_dir(tmp_path / "session")
    paths.directory.mkdir()
    config = PipelineConfig(
        rect=rect, fps=fps, encoder_key=encoder, quality="balanced", ffmpeg=ffmpeg,
        partial_path=paths.video_partial, ffmpeg_log=paths.ffmpeg_log,
    )  # fmt: skip
    errors = errors if errors is not None else []

    def opener(r):
        b = backend or FakeCaptureBackend()
        b.open(r)
        return b

    return RecordingPipeline(config, opener, errors.append), paths


def test_three_second_recording(tmp_path, ffmpeg):
    pipeline, paths = make_pipeline(tmp_path, ffmpeg, Rect(0, 0, 640, 360))
    pipeline.start()
    assert pipeline.wait_ready(5)
    time.sleep(3.0)
    outcome = pipeline.stop()
    assert outcome.error is None
    assert outcome.encoder_exit_code == 0
    assert 85 <= outcome.frames_written <= 95

    info = finalize_to_output(ffmpeg, paths.video_partial, tmp_path / "out", "rec")
    assert info.path.name == "rec.mp4"
    assert (info.width, info.height, info.codec) == (640, 360, "h264")
    assert 2.8 <= info.duration_s <= 3.3
    assert not list((tmp_path / "out").glob(".*vcam-tmp*")), "임시 파일이 남으면 안 된다"


def test_pause_is_removed_from_timeline(tmp_path, ffmpeg):
    pipeline, paths = make_pipeline(tmp_path, ffmpeg, Rect(0, 0, 320, 240))
    pipeline.start()
    assert pipeline.wait_ready(5)
    time.sleep(1.0)
    pipeline.pause()
    time.sleep(1.5)
    pipeline.resume()
    time.sleep(1.0)
    outcome = pipeline.stop()
    assert outcome.error is None
    info = finalize_to_output(ffmpeg, paths.video_partial, tmp_path / "out", "paused")
    assert 1.8 <= info.duration_s <= 2.3


def test_odd_size_is_padded_not_cropped(tmp_path, ffmpeg):
    pipeline, paths = make_pipeline(tmp_path, ffmpeg, Rect(10, 10, 321, 241))
    pipeline.start()
    assert pipeline.wait_ready(5)
    time.sleep(0.8)
    pipeline.stop()
    info = finalize_to_output(ffmpeg, paths.video_partial, tmp_path / "out", "odd")
    assert (info.width, info.height) == (322, 242)


def test_capture_failure_keeps_recorded_part(tmp_path, ffmpeg):
    errors = []
    pipeline, paths = make_pipeline(
        tmp_path, ffmpeg, Rect(0, 0, 320, 240), backend=FakeCaptureBackend(fail_after=40), errors=errors
    )
    pipeline.start()
    assert pipeline.wait_ready(5)
    time.sleep(2.5)
    outcome = pipeline.stop()
    assert outcome.error is not None and outcome.error.code == "capture_lost"
    assert errors and errors[0].code == "capture_lost"
    info = finalize_to_output(ffmpeg, paths.video_partial, tmp_path / "out", "partial", tolerant=True)
    assert info.duration_s > 0.8


def test_partial_mkv_is_recoverable_after_kill(tmp_path, ffmpeg):
    """FFmpeg가 강제 종료돼도 Matroska 중간 파일에서 영상을 살릴 수 있어야 한다."""
    pipeline, paths = make_pipeline(tmp_path, ffmpeg, Rect(0, 0, 320, 240))
    pipeline.start()
    assert pipeline.wait_ready(5)
    time.sleep(2.0)
    pipeline._writer._proc.kill()  # 비정상 종료 흉내
    pipeline.stop()
    info = finalize_to_output(ffmpeg, paths.video_partial, tmp_path / "out", "killed", tolerant=True)
    assert info.duration_s > 0.5


def test_real_encoder_selection(ffmpeg):
    assert select_encoder(ffmpeg).key in {"nvenc", "qsv", "amf", "x264"}


def test_validator_rejects_garbage(tmp_path, ffmpeg):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(Exception):  # noqa: B017
        probe_media(ffmpeg, bad)
