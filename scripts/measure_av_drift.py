"""실제 장치로 N분 녹화하며 A/V 드리프트를 측정한다.

    python scripts/measure_av_drift.py --minutes 10 [--mic]

측정 항목
- 오디오 장치 시계와 세션 시계(영상 기준)의 차이: 녹화 중 최대 오차, 보정량(무음 보충/잘라냄)
- 마무리 직전 오디오 길이 - 영상 길이(끝 오차)
- 최종 MP4의 영상/오디오 스트림 길이 차이
목표: 10분 기준 80ms 이하, 최대 허용 150ms (CLAUDE.md §13)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vcam.capture.factory import open_capture_backend  # noqa: E402
from vcam.capture.region import list_monitors  # noqa: E402
from vcam.domain.models import Rect, SessionPaths  # noqa: E402
from vcam.encoding.encoder_probe import select_encoder  # noqa: E402
from vcam.encoding.ffmpeg import find_ffmpeg  # noqa: E402
from vcam.encoding.muxer import finalize_with_fallback  # noqa: E402
from vcam.services.audio_service import AudioConfig, AudioService  # noqa: E402
from vcam.services.recording_pipeline import PipelineConfig, RecordingPipeline  # noqa: E402

TARGET_MS, LIMIT_MS = 80, 150


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--mic", action="store_true", help="마이크도 함께 측정(마이크 소리가 임시 파일에 기록됩니다)")
    args = parser.parse_args()

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        print("FFmpeg가 없습니다")
        return 2
    work = Path(tempfile.mkdtemp(prefix="vcam-drift-"))
    paths = SessionPaths.in_dir(work / "session")
    paths.directory.mkdir()
    primary = next(m for m in list_monitors() if m.is_primary)
    rect = Rect(primary.rect.left, primary.rect.top, 640, 360)
    config = PipelineConfig(rect, 30, select_encoder(ffmpeg).key, "small", ffmpeg, paths.video_partial, paths.ffmpeg_log)
    audio = AudioService()
    audio.configure(AudioConfig(system_enabled=True, mic_enabled=args.mic))
    problems = audio.wait_ready(5)
    if problems:
        print("오디오 장치 문제:", problems)
    errors: list = []
    pipeline = RecordingPipeline(config, lambda r: open_capture_backend("auto", r), errors.append)
    tracks = audio.begin_recording(pipeline.clock, paths.directory)
    pipeline.start()
    pipeline.wait_ready(10)
    total = args.minutes * 60
    t0 = time.monotonic()
    while (elapsed := time.monotonic() - t0) < total:
        time.sleep(min(30, total - elapsed))
        m = pipeline.metrics()
        print(f"  {(time.monotonic() - t0) / 60:5.1f}분  프레임 {m.frames_written}  드롭 {m.frames_dropped}  {m.measured_fps:.1f}fps", flush=True)
    outcome = pipeline.stop()
    video_s = outcome.frames_written / config.fps
    stats = audio.end_recording(video_s)
    info, notes = finalize_with_fallback(ffmpeg, paths.video_partial, work / "out", "drift", tracks)
    audio.shutdown()

    print(f"\n결과: {info.path}")
    print(f"영상 {info.video_duration_s:.3f}s / 오디오 {info.audio_duration_s:.3f}s "
          f"(스트림 차이 {abs(info.video_duration_s - info.audio_duration_s) * 1000:.0f}ms), "
          f"인코더 {outcome.encoder}, 드롭 {outcome.frames_dropped}, 오류 {errors or '없음'} {notes or ''}")
    worst = 0.0
    for st in stats:
        sr = 48_000
        print(f"[{st.label}] 녹화 중 최대 오차 {st.max_offset_ms:.0f}ms, 끝 오차 {st.end_offset_ms:.0f}ms, "
              f"무음 보충 {st.padded_samples / sr * 1000:.0f}ms, 잘라냄 {st.trimmed_samples / sr * 1000:.0f}ms")
        worst = max(worst, st.max_offset_ms, abs(st.end_offset_ms))
    verdict = "목표 달성" if worst <= TARGET_MS else ("허용 범위" if worst <= LIMIT_MS else "허용 범위 초과")
    print(f"판정: 최악 {worst:.0f}ms → {verdict} (목표 {TARGET_MS}ms, 허용 {LIMIT_MS}ms)")
    return 0 if worst <= LIMIT_MS else 1


if __name__ == "__main__":
    raise SystemExit(main())
