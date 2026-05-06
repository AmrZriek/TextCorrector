import sys
import pytest

import text_corrector as tc


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path")


def test_clipboard_roundtrip_basic_unicode():
    sample = "Σ Ω π μ ∑ √ Δ"
    tc._clipboard_write_text(sample)
    assert tc._clipboard_read_text() == sample


def test_clipboard_roundtrip_emoji():
    sample = "😀🚀✨"  # each = surrogate pair in UTF-16
    tc._clipboard_write_text(sample)
    assert tc._clipboard_read_text() == sample


def test_clipboard_roundtrip_mixed_ascii_unicode():
    sample = "Cost = 42 Ω, area ≈ πr², emoji 🎯 done."
    tc._clipboard_write_text(sample)
    assert tc._clipboard_read_text() == sample


def test_clipboard_roundtrip_empty_string():
    tc._clipboard_write_text("")
    assert tc._clipboard_read_text() == ""
