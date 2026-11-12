import json

from vcam.domain.models import Rect
from vcam.services.profile_service import SCHEMA_VERSION, ProfileService, Settings, migrate


def test_roundtrip(tmp_path):
    svc = ProfileService(tmp_path / "settings.json")
    s = svc.update(Settings(), source_kind="region", last_region=(10, 20, 300, 200))
    loaded = svc.load()
    assert loaded == s
    assert loaded.last_region_rect == Rect(10, 20, 300, 200)


def test_migrates_v1(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schema_version": 1, "fps": 60, "quality": "high", "output_dir": "D:/v"}), encoding="utf-8"
    )
    s = ProfileService(path).load()
    assert s.schema_version == SCHEMA_VERSION
    assert s.profile.fps == 60
    assert s.profile.quality_preset == "high"
    assert s.output_dir == "D:/v"


def test_v2_enables_system_audio(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schema_version": 2, "profile": {"system_audio_enabled": False, "fps": 30}}), encoding="utf-8"
    )
    s = ProfileService(path).load()
    assert s.profile.system_audio_enabled is True
    assert s.profile.microphone_enabled is False
    assert s.system_audio_device == "" and s.mic_device == ""


def test_migrate_is_idempotent():
    data = migrate({"fps": 24})
    assert migrate(dict(data)) == data


def test_broken_file_falls_back_and_is_backed_up(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    s = ProfileService(path).load()
    assert s == Settings(output_dir=s.output_dir)
    assert (tmp_path / "settings.broken.json").exists()


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schema_version": 2, "mystery": 1, "profile": {"fps": 15, "bogus": 2}}), encoding="utf-8"
    )
    assert ProfileService(path).load().profile.fps == 15


def test_editor_same_folder_roundtrip_and_default(tmp_path):
    svc = ProfileService(tmp_path / "settings.json")
    s = svc.update(Settings(), editor_same_folder=False, output_dir="D:/edit")
    assert svc.load() == s
    # 이전 버전 설정 파일(키 없음)은 기본값 True
    (tmp_path / "old.json").write_text(json.dumps({"schema_version": 3, "output_dir": "D:/v"}), encoding="utf-8")
    old = ProfileService(tmp_path / "old.json").load()
    assert old.editor_same_folder is True and old.output_dir == "D:/v"


def test_v3_minimize_on_record_is_turned_off_once(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema_version": 3, "minimize_on_record": True}), encoding="utf-8")
    svc = ProfileService(path)
    assert svc.load().minimize_on_record is False, "0.6.1부터 녹화 중 창 내리기는 기본 해제"
    svc.update(svc.load(), minimize_on_record=True)
    assert ProfileService(path).load().minimize_on_record is True, "사용자가 다시 켜면 유지"
