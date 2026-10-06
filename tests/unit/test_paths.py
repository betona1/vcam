from datetime import datetime

from vcam.util.paths import (
    format_bytes,
    format_duration,
    render_filename,
    sanitize_filename,
    unique_path,
)

NOW = datetime(2026, 10, 6, 9, 5, 7)


def test_default_template():
    assert render_filename("vcam_{yyyy-MM-dd}_{HH-mm-ss}", NOW) == "vcam_2026-10-06_09-05-07"


def test_unknown_tokens_kept_and_invalid_chars_replaced():
    assert render_filename("a:{foo}/{yyyy}", NOW) == "a_{foo}_2026"


def test_reserved_and_empty_names():
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("  ...") == "vcam"


def test_unique_path_never_overwrites(tmp_path):
    first = unique_path(tmp_path, "rec", ".mp4")
    assert first.name == "rec.mp4"
    first.write_bytes(b"x")
    second = unique_path(tmp_path, "rec", ".mp4")
    assert second.name == "rec (2).mp4"
    second.write_bytes(b"x")
    assert unique_path(tmp_path, "rec", ".mp4").name == "rec (3).mp4"


def test_formatting():
    assert format_duration(65) == "01:05"
    assert format_duration(3725) == "1:02:05"
    assert format_bytes(512) == "512 B"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MB"
