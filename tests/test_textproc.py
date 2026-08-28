"""Chunking decides how soon speech starts; sanitising decides if it is bearable."""

from vi_ai.textproc import SentenceChunker, speech_text


def test_markdown_is_stripped():
    text = "## Title\n\n- **Bold** and *italic* with `code` and [link](http://x)\n"
    assert speech_text(text) == "Title\nBold and italic with code and link"


def test_code_fences_are_removed_but_content_kept():
    assert speech_text("```python\nprint(1)\n```") == "print(1)"


def test_sentences_are_emitted_as_they_complete():
    chunker = SentenceChunker()
    assert chunker.feed("The answer is 42") == []  # incomplete: no terminator yet
    assert chunker.feed(". Next up") == ["The answer is 42."]
    assert chunker.flush() == "Next up"


def test_abbreviations_do_not_split_a_sentence():
    chunker = SentenceChunker()
    chunks = chunker.feed("Dr. Smith arrived at 5 p.m. today. Then he left. ")
    assert chunks == ["Dr. Smith arrived at 5 p.m. today.", "Then he left."]


def test_decimals_do_not_split_a_sentence():
    chunker = SentenceChunker()
    assert chunker.feed("Pi is 3.14 roughly. Done. ") == ["Pi is 3.14 roughly.", "Done."]


def test_long_run_on_text_is_split_at_a_word_boundary():
    chunker = SentenceChunker(max_chars=40)
    chunks = chunker.feed("word " * 20)
    assert chunks, "a run-on paragraph must not delay speech indefinitely"
    for chunk in chunks:
        assert len(chunk) <= 40
        assert not chunk.endswith("wor"), "a word was split across utterances"


def test_newlines_end_a_chunk():
    chunker = SentenceChunker()
    assert chunker.feed("First line\nSecond") == ["First line"]


def test_empty_and_markup_only_chunks_are_dropped():
    chunker = SentenceChunker()
    assert chunker.feed("```\n") == []
