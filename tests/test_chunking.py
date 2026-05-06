import text_corrector as tc

def test_chunking_forces_split_on_newlines():
    text = "Line 1.\n\nLine 2.\nLine 3."
    
    # max_words is large enough to fit everything, but it should still split
    # on newlines to preserve formatting (since LLMs strip them).
    chunks = tc._chunk_text_by_sentences(text, 100)
    
    assert chunks == [
        ("Line 1.", "\n\n"),
        ("Line 2.", "\n"),
        ("Line 3.", "")
    ]
