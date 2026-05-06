import sys
import pytest
from unittest.mock import patch, MagicMock

import text_corrector as tc


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path")


def _captured_inputs(call_args):
    """Decode the (count, inputs_array, size) SendInput call back to a list of
    (vk_code, is_keyup) tuples in the order they were submitted."""
    count, inputs_arr, _size = call_args.args
    out = []
    for i in range(count):
        ki = inputs_arr[i].i.ki
        is_keyup = bool(ki.dwFlags & tc.KEYEVENTF_KEYUP)
        out.append((ki.wVk, is_keyup))
    return out


def test_send_ctrl_chord_emits_keydown_c_keyup_in_order():
    with patch.object(tc._user32, "SendInput", return_value=4) as m:
        tc._send_modifier_chord(tc.VK_C, ctrl=True, shift=False)

    assert m.call_count == 1
    seq = _captured_inputs(m.call_args)
    assert seq == [
        (tc.VK_CONTROL, False),
        (tc.VK_C, False),
        (tc.VK_C, True),
        (tc.VK_CONTROL, True),
    ]


def test_send_modifier_chord_handles_insert_for_copy_paste():
    with patch.object(tc._user32, "SendInput", return_value=6) as m:
        # Shift+Insert (paste)
        tc._send_modifier_chord(tc.VK_INSERT, ctrl=False, shift=True)

    seq = _captured_inputs(m.call_args)
    assert seq == [
        (tc.VK_SHIFT, False),
        (tc.VK_INSERT, False),
        (tc.VK_INSERT, True),
        (tc.VK_SHIFT, True),
    ]

    with patch.object(tc._user32, "SendInput", return_value=6) as m2:
        # Ctrl+Insert (copy)
        tc._send_modifier_chord(tc.VK_INSERT, ctrl=True, shift=False)

    seq2 = _captured_inputs(m2.call_args)
    assert seq2 == [
        (tc.VK_CONTROL, False),
        (tc.VK_INSERT, False),
        (tc.VK_INSERT, True),
        (tc.VK_CONTROL, True),
    ]
