from vcam.editing.formats import CONTAINERS, EncodeSettings, audio_args, video_quality_args
from vcam.editing.segments import (
    Segment,
    complement,
    format_time,
    normalize,
    snap_to_keyframes,
    split_at,
    split_equal,
    split_every,
    total_duration,
)


def test_normalize_merges_overlaps_and_clamps():
    segs = [Segment(5, 7), Segment(1, 3), Segment(2.5, 4), Segment(9, 20), Segment(6, 6.01)]
    assert normalize(segs, 10) == [Segment(1, 4), Segment(5, 7), Segment(9, 10)]


def test_complement_for_segment_removal():
    assert complement([Segment(2, 4), Segment(6, 7)], 10) == [Segment(0, 2), Segment(4, 6), Segment(7, 10)]
    assert complement([Segment(0, 10)], 10) == []
    assert complement([], 10) == [Segment(0, 10)]


def test_split_variants():
    assert split_equal(4, 10) == [Segment(0, 2.5), Segment(2.5, 5), Segment(5, 7.5), Segment(7.5, 10)]
    assert split_every(3, 10) == [Segment(0, 3), Segment(3, 6), Segment(6, 9), Segment(9, 10)]
    assert split_at([7, 2, 2, 0, 10], 10) == [Segment(0, 2), Segment(2, 7), Segment(7, 10)]
    assert total_duration(split_equal(7, 10)) == 10


def test_snap_to_previous_keyframe():
    keys = [0.0, 1.0, 2.0, 3.0]
    assert snap_to_keyframes([Segment(2.4, 5), Segment(0.5, 1)], keys) == [Segment(2.0, 5), Segment(0.0, 1)]
    assert snap_to_keyframes([Segment(2.0, 5)], keys) == [Segment(2.0, 5)]


def test_format_time():
    assert format_time(65.25) == "01:05.250"
    assert format_time(3725.5, millis=False) == "1:02:05"


def test_settings_fix_invalid_combinations():
    s = EncodeSettings(container="webm", video_codec="h264", audio_codec="aac").fixed()
    assert (s.video_codec, s.audio_codec) == ("vp9", "opus")
    assert EncodeSettings(speed=10).fixed().speed == 4.0
    assert all(c.video for c in CONTAINERS.values())


def test_quality_and_audio_args():
    assert video_quality_args("libx264", EncodeSettings(quality=100))[:2] == ["-crf", "15"]
    assert video_quality_args("mpeg4", EncodeSettings(quality=100)) == ["-q:v", "2"]
    assert "-maxrate" in video_quality_args("libx264", EncodeSettings(rate_control="cbr", bitrate_kbps=5000))
    opus = audio_args(EncodeSettings(audio_codec="opus", sample_rate=44100))
    assert opus[opus.index("-ar") + 1] == "48000", "Opus는 44.1kHz를 지원하지 않는다"
    assert "-b:a" not in audio_args(EncodeSettings(audio_codec="flac"))


def test_settings_roundtrip():
    s = EncodeSettings(container="mkv", speed=1.5, rotate=90)
    assert EncodeSettings.from_dict(s.to_dict() | {"unknown": 1}) == s
