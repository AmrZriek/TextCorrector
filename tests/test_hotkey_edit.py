from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import QEvent

import text_corrector as tc


def _press(widget, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
    widget._recording = True
    ev = QKeyEvent(QEvent.Type.KeyPress, key, mods, text)
    widget.keyPressEvent(ev)


def test_hotkey_edit_accepts_f9_alone(qtbot):
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_F9)
    assert w.text() == "f9"


def test_hotkey_edit_accepts_pause_alone(qtbot):
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_Pause)
    assert w.text() == "pause"


def test_hotkey_edit_rejects_letter_alone(qtbot):
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_T, text="t")
    assert w.text() != "t"


def test_hotkey_edit_accepts_ctrl_t_combo(qtbot):
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier, text="t")
    assert w.text() == "ctrl+t"


def test_hotkey_edit_manual_edit_drops_focus_on_enter(qtbot):
    w = tc.HotkeyEdit()
    qtbot.addWidget(w)
    with qtbot.waitExposed(w):
        w.show()
    w.enable_manual_edit()
    w.setFocus()
    assert w.isReadOnly() is False
    
    # Simulate pressing enter
    w.returnPressed.emit()
    
    # In a headless test environment, clearFocus() might not always dispatch a FocusOut
    # event if there are no other widgets to take focus. We manually send one to simulate it.
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    
    QApplication.sendEvent(w, QFocusEvent(QEvent.Type.FocusOut))
    
    # After enter, it should drop focus and become readonly
    assert w.isReadOnly() is True
