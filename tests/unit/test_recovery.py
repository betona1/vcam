import json

from vcam.services.recovery_service import find_incomplete_sessions


def test_finds_partial_and_cleans_empty(tmp_path):
    good = tmp_path / "20261006-1"
    good.mkdir()
    (good / "video.partial.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"0" * 100)
    (good / "session.json").write_text(
        json.dumps({"started_at": "2026-10-06T09:00:00", "output_stem": "x"}), encoding="utf-8"
    )
    empty = tmp_path / "20261006-2"
    empty.mkdir()
    (empty / "video.partial.mkv").write_bytes(b"")
    nothing = tmp_path / "20261006-3"
    nothing.mkdir()

    found = find_incomplete_sessions(tmp_path)
    assert [s.paths.directory for s in found] == [good]
    assert found[0].started_label == "2026-10-06 09:00:00"
    assert not empty.exists()
    assert not nothing.exists()


def test_excludes_active_session(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    (active / "video.partial.mkv").write_bytes(b"data")
    assert find_incomplete_sessions(tmp_path, exclude=active) == []
