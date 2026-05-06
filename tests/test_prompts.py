import pytest
from text_corrector import _SENTENCE_REWRITE_PROMPT, _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
from text_corrector import CorrectionWindow # to access inline streaming prompts

def test_conservative_prompt_has_fewshot():
    # Does the conservative prompt contain any <<<START>>> examples?
    assert "<<<START>>>" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "EXAMPLES:" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE

def test_conservative_prompt_abstract_terms_count():
    assert "wording" not in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "tone" not in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "intent" not in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "style" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "word choice" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE

def test_streaming_conservative_rules_differ_from_patch():
    # streaming prompts are inside CorrectionWindow._start_streaming_correction
    import inspect
    from text_corrector import CorrectionWindow
    source = inspect.getsource(CorrectionWindow._start_streaming_correction)
    # The streaming prompt uses <<<TEXT>>> instead of <<<START>>>
    assert "<<<TEXT>>>" in source
    # Patch prompt uses <<<START>>>
    assert "<<<START>>>" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE

def test_prompt_word_count_budget():
    words = len(_SENTENCE_REWRITE_PROMPT_CONSERVATIVE.split())
    # Should be well under 1000 words (budget is ~3200 tokens)
    assert words < 300
