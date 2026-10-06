"""RecordingController 전 구간: 상태 머신 → 작업 스레드 → FFmpeg → 검증된 MP4."""

import pytest

from vcam.audio.base import FakeAudioSource
from vcam.capture.region import list_monitors
from vcam.domain.models import CaptureSource, RecordingProfile, Rect
from vcam.domain.states import RecordingState
from vcam.services.audio_service import AudioService
from vcam.services.profile_service import Settings
from vcam.services.recording_controller import RecordingController

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def fake_audio():
    return AudioService(lambda kind, _d: FakeAudioSource(channels=2 if kind == "system" else 1))


def make_controller(tmp_path, audio=None, mic=True):
    settings = Settings(
        output_dir=str(tmp_path / "out"), countdown_s=1, capture_backend="mss",
        profile=RecordingProfile(fps=30, encoder_preference="x264", system_audio_enabled=True, microphone_enabled=mic),
    )  # fmt: skip
    c = RecordingController(settings, audio=audio or fake_audio())
    primary = next(m for m in list_monitors() if m.is_primary)
    rect = Rect(primary.rect.left + 50, primary.rect.top + 50, 400, 300)
    c.set_source(CaptureSource("region", rect, "테스트 영역"))
    assert c.state is RecordingState.READY
    return c


@pytest.fixture
def controller(qtbot, tmp_path, ffmpeg):
    c = make_controller(tmp_path)
    yield c
    c.shutdown()


def test_record_pause_stop_saves_mp4(qtbot, controller, tmp_path):
    states = []
    controller.state_changed.connect(states.append)
    controller.start()
    assert controller.state is RecordingState.COUNTDOWN
    qtbot.waitUntil(lambda: controller.state is RecordingState.RECORDING, timeout=10_000)
    qtbot.wait(1500)
    controller.toggle_pause()
    assert controller.state is RecordingState.PAUSED
    qtbot.wait(1000)
    controller.toggle_pause()
    qtbot.wait(1000)
    with qtbot.waitSignal(controller.recording_saved, timeout=30_000) as saved:
        controller.stop()
    result = saved.args[0]
    assert controller.state is RecordingState.REVIEW
    assert result.path.exists() and result.path.suffix == ".mp4"
    assert (result.media.width, result.media.height) == (400, 300)
    assert 2.0 <= result.media.duration_s <= 3.0, "일시정지 1초는 결과에서 빠져야 한다"
    assert not result.recovered
    assert result.media.audio_codec == "aac", "시스템 소리 + 마이크가 합쳐져야 한다"
    assert abs(result.media.audio_duration_s - result.media.video_duration_s) < 0.1
    assert states[:3] == [RecordingState.COUNTDOWN, RecordingState.RECORDING, RecordingState.PAUSED]
    assert not list((tmp_path / "localappdata" / "vcam" / "sessions").iterdir()), "세션 임시 폴더는 정리돼야 한다"


def test_countdown_can_be_cancelled(qtbot, controller):
    controller.start()
    controller.toggle_start_stop()
    assert controller.state is RecordingState.READY
    qtbot.wait(1500)
    assert controller.state is RecordingState.READY


def test_invalid_command_is_ignored_not_crashing(controller):
    controller.toggle_pause()  # READY에서 일시정지는 무시된다
    controller.stop()
    assert controller.state is RecordingState.READY


def test_missing_ffmpeg_reports_korean_error(qtbot, controller, monkeypatch):
    monkeypatch.setattr("vcam.services.recording_controller.find_ffmpeg", lambda _p=None: None)
    with qtbot.waitSignal(controller.error_raised) as err:
        controller.start()
    assert err.args[0].code == "ffmpeg_missing"
    assert "FFmpeg" in err.args[0].message
    assert controller.state is RecordingState.READY


def test_real_system_audio_loopback(qtbot, tmp_path, ffmpeg):
    """실제 WASAPI 루프백(기본 출력 장치)으로 3초 녹화한다. 소리가 없으면 무음 트랙이 들어간다."""
    c = make_controller(tmp_path, audio=AudioService(), mic=False)
    try:
        if c.audio.wait_ready(5):
            pytest.skip("이 PC에서 시스템 소리 장치를 열 수 없습니다")
        c.start()
        qtbot.waitUntil(lambda: c.state is RecordingState.RECORDING, timeout=10_000)
        qtbot.wait(3000)
        with qtbot.waitSignal(c.recording_saved, timeout=30_000) as saved:
            c.stop()
        media = saved.args[0].media
        assert media.audio_codec == "aac"
        assert abs(media.audio_duration_s - media.video_duration_s) < 0.1
    finally:
        c.shutdown()


def test_finalize_exception_does_not_hang_ui(qtbot, controller, monkeypatch):
    """마무리 중 예기치 않은 오류(예: 디스크 가득 참)가 나도 '저장 중'에 멈추지 않는다."""
    errors = []
    controller.error_raised.connect(errors.append)
    controller.start()
    qtbot.waitUntil(lambda: controller.state is RecordingState.RECORDING, timeout=10_000)
    qtbot.wait(800)

    def boom(_duration):
        raise OSError("디스크 가득 참(테스트)")

    monkeypatch.setattr(controller.audio, "end_recording", boom)
    controller.stop()
    qtbot.waitUntil(lambda: controller.state is RecordingState.ERROR, timeout=30_000)
    assert errors and errors[-1].code == "save_failed"


def test_pause_immediately_after_start_is_honored(qtbot, controller):
    controller.start()
    qtbot.waitUntil(lambda: controller.state is RecordingState.RECORDING, timeout=10_000)
    controller.toggle_pause()  # 캡처가 아직 열리는 중일 수 있다
    assert controller.state is RecordingState.PAUSED
    qtbot.wait(1200)
    assert controller._pipeline.metrics().frames_written <= 2, "일시정지 중에는 프레임이 쌓이면 안 된다"
    controller.toggle_pause()
    qtbot.wait(1000)
    with qtbot.waitSignal(controller.recording_saved, timeout=30_000) as saved:
        controller.stop()
    assert 0.6 <= saved.args[0].media.duration_s <= 1.5


def test_shutdown_during_finalize_saves_once(qtbot, controller, tmp_path):
    controller.start()
    qtbot.waitUntil(lambda: controller.state is RecordingState.RECORDING, timeout=10_000)
    qtbot.wait(1000)
    controller.stop()
    assert controller.state is RecordingState.FINALIZING
    controller.shutdown()  # 앱 종료: 저장이 끝나기를 기다려야 하고 두 번 저장하면 안 된다
    files = list((tmp_path / "out").glob("*.mp4"))
    assert len(files) == 1, files
    assert not list((tmp_path / "out").glob(".*vcam-tmp*"))
