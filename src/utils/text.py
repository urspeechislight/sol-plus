"""
Arabic text normalization utilities.

Normalization rules are applied in a fixed order.
Every rule is explicit — no "smart" fallbacks that hide encoding problems.
If the input cannot be normalized, the caller receives the exception.
"""

from __future__ import annotations

import re
import unicodedata

_ARABIC_DIACRITICS = re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7-\u06E8\u06EA-\u06ED]"
)

_TATWEEL = re.compile(r"\u0640+")

_REPEATED_SPACES = re.compile(r" {2,}")

_REPEATED_NEWLINES = re.compile(r"\n{3,}")

_WINDOWS_NEWLINES = re.compile(r"\r\n|\r")


def normalize(text: str) -> str:
    """Normalize Arabic text for pipeline processing.

    Applies in order:
      1. Unicode NFC normalization
      2. Windows line endings → Unix
      3. Strip tatweel (kashida)
      4. Strip diacritics (tashkeel)
      5. Collapse repeated spaces
      6. Collapse 3+ newlines to 2
      7. Strip leading/trailing whitespace

    Does not lowercase (Arabic has no case).
    Does not remove punctuation (punctuation carries structural signal).
    """
    text = unicodedata.normalize("NFC", text)
    text = _WINDOWS_NEWLINES.sub("\n", text)
    text = _TATWEEL.sub("", text)
    text = _ARABIC_DIACRITICS.sub("", text)
    text = _REPEATED_SPACES.sub(" ", text)
    text = _REPEATED_NEWLINES.sub("\n\n", text)
    return text.strip()
