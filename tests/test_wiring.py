"""Source-level wiring assertions: confirms call sites use the Win32-safe
helpers instead of the higher-level libraries that exhibit the symptoms
documented in AGENT_CONTEXT.md Rule #21."""
from pathlib import Path
import re

SRC = (Path(__file__).resolve().parent.parent / "text_corrector.py").read_text(encoding="utf-8")


def test_hotkey_worker_uses_send_ctrl_chord_for_copy():
    # The body of _hotkey_worker must call _send_ctrl_chord(VK_C), not keyboard.send('ctrl+c').
    body = re.search(r"def _hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_send_modifier_chord" in body
    assert "keyboard.send('ctrl+c')" not in body
    assert "keyboard.send(\"ctrl+c\")" not in body


def test_paste_text_uses_send_ctrl_chord_for_paste():
    body = re.search(r"def _paste_text\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_send_modifier_chord" in body
    assert "keyboard.send('ctrl+v')" not in body
    assert "keyboard.send(\"ctrl+v\")" not in body


def test_safe_paste_uses_clipboard_helper():
    body = re.search(r"def _safe_paste\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_clipboard_read_text" in body


def test_safe_copy_uses_clipboard_helper():
    body = re.search(r"def _safe_copy\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_clipboard_write_text" in body
