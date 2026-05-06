"""Tests for CORE_TEMPLATES, DEFAULT_CONFIG, and the OSD widget class."""
import text_corrector as tc


def test_core_templates_is_module_level():
    """DEFAULT_TEMPLATES should be accessible as a module-level constant."""
    assert hasattr(tc, "DEFAULT_TEMPLATES")
    assert isinstance(tc.DEFAULT_TEMPLATES, list)
    assert len(tc.DEFAULT_TEMPLATES) >= 5


def test_core_templates_are_tuples():
    """Each entry should be a dict with name and prompt strings."""
    for entry in tc.DEFAULT_TEMPLATES:
        assert isinstance(entry, dict)
        name, prompt = entry["name"], entry["prompt"]
        assert isinstance(name, str) and len(name) > 0
        assert isinstance(prompt, str) and len(prompt) > 10


def test_default_config_has_silent_keys():
    """DEFAULT_CONFIG should contain silent_hotkey and silent_strength."""
    assert "silent_hotkey" in tc.DEFAULT_CONFIG
    assert "silent_strength" in tc.DEFAULT_CONFIG
    assert tc.DEFAULT_CONFIG["silent_strength"] in ("conservative", "smart_fix")


def test_default_config_strength_values():
    """Strength config values should be valid."""
    assert tc.DEFAULT_CONFIG["streaming_strength"] in ("conservative", "smart_fix")
    assert tc.DEFAULT_CONFIG["silent_strength"] in ("conservative", "smart_fix")


def test_osd_instantiation_success(qtbot):
    """SilentCorrectionOSD can be created in 'success' state without crashing."""
    osd = tc.SilentCorrectionOSD("Test message", state="success")
    assert osd.windowFlags() & tc.Qt.WindowType.FramelessWindowHint
    assert osd._state == "success"
    osd.close()


def test_osd_instantiation_loading(qtbot):
    """SilentCorrectionOSD can be created in 'loading' state."""
    osd = tc.SilentCorrectionOSD("Working...", state="loading")
    assert osd._state == "loading"
    osd.close()


def test_osd_instantiation_warning(qtbot):
    """SilentCorrectionOSD can be created in 'warning' state."""
    osd = tc.SilentCorrectionOSD("Error!", state="warning")
    assert osd._state == "warning"
    osd.close()
