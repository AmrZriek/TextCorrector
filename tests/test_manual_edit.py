"""Tests for HotkeyEdit manual edit mode."""
from PyQt6.QtCore import Qt
import text_corrector as tc


def test_manual_edit_sets_editable(qtbot):
    """enable_manual_edit() should switch to editable (non-read-only) state."""
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    
    w.enable_manual_edit()
    
    assert not w.isReadOnly()
    assert w._manual_editing is True
    assert w._recording is False
    w.close()


def test_manual_edit_escape_cancels(qtbot):
    """Pressing Escape during manual edit should cancel without changing the combo."""
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    original = w.text()
    
    w.enable_manual_edit()
    qtbot.keyPress(w, Qt.Key.Key_Escape)
    
    assert w.isReadOnly()
    assert w._manual_editing is False
    assert w.text() == original
    w.close()


def test_manual_edit_commit_valid(qtbot):
    """Typing a valid hotkey and pressing Enter should commit it."""
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    
    w.enable_manual_edit()
    w.clear()
    qtbot.keyClicks(w, "ctrl+f10")
    qtbot.keyPress(w, Qt.Key.Key_Return)
    
    assert w.isReadOnly()
    assert w._manual_editing is False
    assert w.text() == "ctrl+f10"
    w.close()


def test_manual_edit_commit_empty_reverts(qtbot):
    """Committing empty text should revert to the previous combo."""
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    
    w.enable_manual_edit()
    w.clear()
    # Commit with empty text
    w._commit_manual_edit()
    
    assert w.isReadOnly()
    assert w.text() == "f9"
    w.close()


def test_manual_edit_fires_signal(qtbot):
    """Committing a valid manual edit should emit shortcut_changed."""
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    
    received = []
    w.shortcut_changed.connect(received.append)
    
    w.enable_manual_edit()
    w.clear()
    qtbot.keyClicks(w, "ctrl+shift+a")
    qtbot.keyPress(w, Qt.Key.Key_Return)
    
    assert len(received) == 1
    assert received[0] == "ctrl+shift+a"
    w.close()
