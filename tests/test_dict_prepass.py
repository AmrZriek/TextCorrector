import pytest
from text_corrector import _dict_prepass, _COMMON_TYPOS_MAP, ModelManager

def test_dict_prepass_case_preservation():
    assert _dict_prepass("teh")[0] == "the"
    assert _dict_prepass("Teh")[0] == "The"
    assert _dict_prepass("TEH")[0] == "THE"

def test_dict_fixes_are_always_safe_for_conservative():
    # Check if they are single word (no spaces in keys)
    # Actually there might be some keys with spaces? Let's check
    for k, v in _COMMON_TYPOS_MAP.items():
        assert " " not in k or k in ["arn't"], f"Key '{k}' is not a single word typo"

class MockConfig:
    def get(self, key, default=None): return default

def test_dict_prepass_fast_path_bypasses_llm_on_dirty():
    # The test for fast path. We can instantiate ModelManager and call correct_text_patch
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    # "teh test" -> 2 words. < 15 words.
    # It must start uppercase and end with .!? to hit the fast path
    res, units = mgr.correct_text_patch("Teh test.")
    assert units == 0 # no LLM called
    assert res == "The test."

def test_dict_prepass_fast_path_structural_requirement_blocks():
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    
    # Needs to be > 0 dict fixes
    # Doesn't start with uppercase
    import threading
    class MockEvent:
        def is_set(self): return False
    
    # We will monkeypatch _chunk_text_by_sentences to just return the chunk
    # so it skips actual LLM call and we can see units = 1
    # We will just patch _rewrite_sentence_chunk
    mgr._rewrite_sentence_chunk = lambda *args, **kwargs: "<<<START>>>the test<<<END>>>"
    
    res, units = mgr.correct_text_patch("teh test")
    # Because 't' is not uppercase, it fails structural_clean check, goes to LLM
    assert units == 1
