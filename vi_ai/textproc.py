"""Preparing model output for a speech engine.

Two jobs:

* `speech_text` strips markup that a speech engine would either read out as
  noise ("star star important star star") or silently swallow.
* `SentenceChunker` splits a stream of deltas into sentences, so speech starts
  a second or two after the model does instead of after the whole answer.
"""

from __future__ import annotations

import re

# Words that end in a period without ending a sentence.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "eg", "ie", "approx", "dept", "est", "fig", "inc", "ltd", "no", "vol",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "am", "pm",
})

_SENTENCE_ENDINGS = ".!?"
# Closing marks that belong to the sentence they follow.
_TRAILING_MARKS = "\"')]}”’"

_CODE_FENCE = re.compile(r"^\s*```.*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_HORIZONTAL_RULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL)
_INLINE_CODE = re.compile(r"`+([^`]+)`+")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{2,}")


def speech_text(text: str) -> str:
    """Strip markup so a speech engine reads prose, not punctuation soup."""
    if not text:
        return ""
    result = _CODE_FENCE.sub("", text)
    result = _HORIZONTAL_RULE.sub("", result)
    result = _LINK.sub(r"\1", result)  # keep the label, drop the URL
    result = _INLINE_CODE.sub(r"\1", result)
    result = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", result)
    result = _ITALIC.sub(r"\1", result)
    result = _HEADING.sub("", result)
    result = _BLOCKQUOTE.sub("", result)
    result = _BULLET.sub("", result)
    result = _WHITESPACE.sub(" ", result)
    result = _BLANK_LINES.sub("\n", result)
    return result.strip()


def _ends_abbreviation(buffer: str, period_index: int) -> bool:
    """True if the period at `period_index` looks like part of an abbreviation."""
    start = period_index
    while start > 0 and (buffer[start - 1].isalnum() or buffer[start - 1] == "."):
        start -= 1
    word = buffer[start:period_index].replace(".", "").lower()
    if not word:
        return False
    # A lone letter is almost always an initial ("J. Smith", "e. g.").
    if len(word) == 1 and word.isalpha():
        return True
    return word in _ABBREVIATIONS


class SentenceChunker:
    """Accumulates streamed text and yields speakable chunks.

    A chunk is emitted at a sentence boundary, at a line break, or once the
    buffer grows past `max_chars` (long chunks are slow to interrupt, and a
    model that writes a run-on paragraph should not delay speech indefinitely).
    """

    def __init__(self, max_chars: int = 300) -> None:
        self.max_chars = max(20, max_chars)
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        """Add streamed text; return any chunks that are now complete."""
        self._buffer += text
        chunks: list[str] = []
        while True:
            index = self._split_index()
            if index is None:
                break
            chunk, self._buffer = self._buffer[:index], self._buffer[index:]
            prepared = speech_text(chunk)
            if prepared:
                chunks.append(prepared)
        return chunks

    def flush(self) -> str:
        """Return whatever is left, and reset."""
        remainder, self._buffer = self._buffer, ""
        return speech_text(remainder)

    def _split_index(self) -> int | None:
        buffer = self._buffer
        for i, char in enumerate(buffer):
            if char == "\n":
                return i + 1
            if char not in _SENTENCE_ENDINGS:
                continue
            j = i + 1
            while j < len(buffer) and buffer[j] in _SENTENCE_ENDINGS + _TRAILING_MARKS:
                j += 1
            if j >= len(buffer):
                # More text may follow; wait to see whether a space comes next.
                break
            if not buffer[j].isspace():
                continue
            if char == "." and _ends_abbreviation(buffer, i):
                continue
            return j

        if len(buffer) >= self.max_chars:
            # Break at the last word boundary that fits, so a word is not split
            # across two utterances.
            cut = buffer.rfind(" ", 0, self.max_chars)
            return cut + 1 if cut > 0 else self.max_chars
        return None
