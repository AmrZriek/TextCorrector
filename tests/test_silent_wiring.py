"""Source-level wiring tests for the silent correction feature and new config keys."""
from pathlib import Path
import re

SRC = (Path(__file__).resolve().parent.parent / "text_corrector.py").read_text(encoding="utf-8")


def test_silent_worker_uses_send_ctrl_chord():
    """_silent_hotkey_worker must use Win32 SendInput for copy and paste."""
    body = re.search(
        r"def _silent_hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_send_modifier_chord" in body
    assert "_send_modifier_chord" in body
    assert "keyboard.send" not in body


def test_silent_worker_uses_clipboard_helpers():
    """_silent_hotkey_worker must use the Win32-safe clipboard wrappers."""
    body = re.search(
        r"def _silent_hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_safe_copy" in body
    assert "_safe_paste" in body
    assert "pyperclip.copy" not in body


def test_silent_worker_no_qtimer_from_thread():
    """_silent_hotkey_worker must NOT call QTimer.singleShot (thread-unsafe crash)."""
    body = re.search(
        r"def _silent_hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    # Check for actual call pattern (with opening paren), not comment mentions
    assert "QTimer.singleShot(" not in body


def test_silent_worker_reads_silent_strength():
    """_silent_hotkey_worker should read 'silent_strength', not 'streaming_strength'."""
    body = re.search(
        r"def _silent_hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "silent_strength" in body
    assert "streaming_strength" not in body


def test_silent_hotkey_registered_in_register_hotkey():
    """_register_hotkey must register the silent hotkey alongside the main one."""
    body = re.search(
        r"def _register_hotkey\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "silent_hotkey" in body
    assert "_silent_hotkey_signal" in body


def test_copy_method_uses_clipboard_helper():
    """CorrectionWindow._copy must use _clipboard_write_text, not pyperclip."""
    body = re.search(
        r"def _copy\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_clipboard_write_text" in body
    assert "pyperclip.copy" not in body
