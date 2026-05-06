import pytest
from text_corrector import _hallucination_ratio, _HALLUCINATION_THRESHOLD_CONSERVATIVE, _HALLUCINATION_THRESHOLD_SMARTFIX

def test_ratio_identical_text():
    assert _hallucination_ratio("hello world", "hello world") == 0.0

def test_ratio_completely_different():
    assert _hallucination_ratio("hello world", "foo bar") == 1.0

def test_ratio_single_typo_vs_replacement():
    """
    Hypothesis: Word-level SequenceMatcher is blind to typo vs. replacement.
    """
    # Typo
    ratio_typo = _hallucination_ratio("i beleive it", "i believe it")
    
    # Replacement
    ratio_repl = _hallucination_ratio("i caterpiller it", "i believe it")
    
    # SequenceMatcher at word level sees both as 1 word changed out of 3.
    # Since difflib doesn't look at character-level similarity when split() is used:
    assert ratio_typo == ratio_repl

def test_ratio_short_3word_sentence_33pct():
    """
    1 word changed in a 3 word sentence = 33% drift.
    """
    orig = "I am happy"
    corr = "I was happy"
    ratio = _hallucination_ratio(orig, corr)
    # difflib ratio for ["I", "am", "happy"] vs ["I", "was", "happy"]
    # matches 2 out of 3 words. ratio is 2 * 2 / (3 + 3) = 4/6 = 0.666
    # 1 - 0.666 = 0.333
    assert abs(ratio - 0.333) < 0.01
    assert ratio <= _HALLUCINATION_THRESHOLD_CONSERVATIVE

def test_ratio_short_2word_sentence_50pct():
    """
    1 word changed in a 2 word sentence = 50% drift.
    """
    orig = "Hello world"
    corr = "Hi world"
    ratio = _hallucination_ratio(orig, corr)
    # difflib ratio for ["Hello", "world"] vs ["Hi", "world"]
    # matches 1 out of 2 words. ratio is 2 * 1 / (2 + 2) = 2/4 = 0.5
    # 1 - 0.5 = 0.5
    assert abs(ratio - 0.5) < 0.01
    
    # It FAILS conservative guard
    assert ratio > _HALLUCINATION_THRESHOLD_CONSERVATIVE
    
    # It passes smart_fix guard (0.6)
    assert ratio <= _HALLUCINATION_THRESHOLD_SMARTFIX
