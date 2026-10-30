"""편집기 저장 위치가 메인 창의 저장 폴더 설정과 연결되는지 확인한다 (FFmpeg 불필요)."""

from vcam.ui.editor.editor_window import EditorWindow
from vcam.ui.theme import apply_theme


def make_editor(qtbot, qapp):
    w = EditorWindow(apply_theme(qapp, "dark"), lambda: None)
    qtbot.addWidget(w)
    return w


def test_set_output_dir_follows_settings_without_echo(qtbot, qapp):
    w = make_editor(qtbot, qapp)
    received = []
    w.output_dir_changed.connect(received.append)
    w.set_output_dir("D:/from-main")
    assert w.out_dir.text() == "D:/from-main"
    assert received == []  # 메인 창이 넣어 준 값은 다시 신호로 내보내지 않는다


def test_edited_output_dir_emits_once(qtbot, qapp):
    w = make_editor(qtbot, qapp)
    w.set_output_dir("D:/a")
    received = []
    w.output_dir_changed.connect(received.append)
    w.set_same_folder(False)
    w.out_dir.setText("D:/b")
    w.out_dir.editingFinished.emit()
    w.out_dir.editingFinished.emit()  # 같은 값은 한 번만
    w.out_dir.setText("")
    w.out_dir.editingFinished.emit()  # 빈 값은 무시
    assert received == ["D:/b"]


def test_same_folder_toggle_persists_via_signal(qtbot, qapp):
    w = make_editor(qtbot, qapp)
    received = []
    w.same_folder_changed.connect(received.append)
    w.set_same_folder(False)  # 설정에서 복원할 때는 신호 없음
    assert received == [] and w.out_dir.isEnabled()
    w.same_folder.setChecked(True)
    assert received == [True] and not w.out_dir.isEnabled()
