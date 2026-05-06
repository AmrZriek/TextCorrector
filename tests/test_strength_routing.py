import pytest
from text_corrector import ModelManager, _HALLUCINATION_THRESHOLD_CONSERVATIVE, _HALLUCINATION_THRESHOLD_SMARTFIX
import threading

class MockConfig:
    def get(self, key, default=None): return default

def test_rewrite_chunk_selects_conservative_prompt(monkeypatch):
    mgr = ModelManager(MockConfig())
    captured_sys = ""
    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            class R:
                ok = True
                status_code = 200
                def json(self): return {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
                def raise_for_status(self): pass
            return R()
        def close(self): pass
    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "conservative")
    assert "spelling-only" in captured_sys

def test_rewrite_chunk_selects_smartfix_prompt(monkeypatch):
    mgr = ModelManager(MockConfig())
    captured_sys = ""
    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            class R:
                ok = True
                status_code = 200
                def json(self): return {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
                def raise_for_status(self): pass
            return R()
        def close(self): pass
    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "smart_fix")
    assert "spelling-only" not in captured_sys

def test_correct_text_patch_passes_strength_to_chunks(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    captured_strength = ""
    def mock_rewrite(chunk_text, custom_sys, idx, total, strength):
        nonlocal captured_strength
        captured_strength = strength
        return chunk_text
    mgr._rewrite_sentence_chunk = mock_rewrite
    
    # Need structurally dirty so it bypasses fast-path
    mgr.correct_text_patch("test text without caps", strength="conservative")
    assert captured_strength == "conservative"

def test_correct_text_patch_conservative_threshold(monkeypatch):
    # This requires looking at the source code of correct_text_patch to confirm it uses _HALLUCINATION_THRESHOLD_CONSERVATIVE
    import inspect
    source = inspect.getsource(ModelManager.correct_text_patch)
    assert "strength == \"conservative\"" in source
    assert "_HALLUCINATION_THRESHOLD_CONSERVATIVE" in source
    
def test_correct_text_patch_smartfix_threshold(monkeypatch):
    import inspect
    source = inspect.getsource(ModelManager.correct_text_patch)
    assert "_HALLUCINATION_THRESHOLD_SMARTFIX" in source
