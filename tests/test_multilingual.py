def test_multilingual_config_default():
    import text_corrector as tc
    assert tc.DEFAULT_CONFIG.get("target_language") == "English"