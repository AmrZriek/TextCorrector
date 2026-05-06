import pytest
from text_corrector import ConfigManager

def test_config_manager_populates_default_templates(monkeypatch, tmp_path):
    # Use a temporary config file
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("text_corrector.CONFIG_FILE", config_file)
    
    mgr = ConfigManager()
    templates = mgr.config.get("custom_templates", [])
    
    assert len(templates) >= 5
    assert templates[0]["name"] == "📧 Email"
    assert "professional email" in templates[0]["prompt"]
    assert "Do not add greetings or closings if they are not already present." in templates[0]["prompt"]
    
    # Save and reload should preserve
    mgr.save()
    mgr2 = ConfigManager()
    assert len(mgr2.config.get("custom_templates", [])) == len(templates)
