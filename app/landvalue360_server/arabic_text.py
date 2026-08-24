"""Pure-Python Arabic shaping and visual-order fallback for Pillow BASIC layout.

Pillow normally delegates Arabic shaping and bidirectional layout to RAQM.
Some Windows Pillow builds do not include RAQM, so this module derives Arabic
presentation forms from Python's bundled Unicode database and converts logical
RTL text to a display-order string that can be painted by the BASIC layout
engine.  No external DLLs, fonts, or system packages are required.
"""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
_PRESENTATION_RANGES = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_FORM_TAGS = {"<isolated>": "isolated", "<final>": "final", "<initial>": "initial", "<medial>": "medial"}
_ZWJ = "\u200d"
_ZWNJ = "\u200c"
_TATWEEL = "\u0640"
_MIRROR = str.maketrans({"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"})


def contains_arabic(text: str) -> bool:
    return any(any(start <= ord(char) <= end for start, end in _ARABIC_RANGES) for char in str(text))


@lru_cache(maxsize=1)
def _forms() -> dict[str, dict[str, str]]:
    forms: dict[str, dict[str, str]] = {}
    for start, end in _PRESENTATION_RANGES:
        for codepoint in range(start, end + 1):
            glyph = chr(codepoint)
            decomposition = unicodedata.decomposition(glyph)
            if not decomposition:
                continue
            parts = decomposition.split()
            form = _FORM_TAGS.get(parts[0])
            if form is None or len(parts) != 2:
                continue
            base = chr(int(parts[1], 16))
            forms.setdefault(base, {})[form] = glyph
    return forms


def _transparent(char: str) -> bool:
    return bool(unicodedata.combining(char)) or unicodedata.bidirectional(char) == "NSM"


def _can_join_previous(char: str) -> bool:
    if char == _TATWEEL:
        return True
    data = _forms().get(char) or {}
    return "final" in data or "medial" in data


def _can_join_next(char: str) -> bool:
    if char == _TATWEEL:
        return True
    data = _forms().get(char) or {}
    return "initial" in data or "medial" in data


def _neighbor(chars: list[str], index: int, step: int) -> tuple[int | None, bool]:
    """Return the next non-transparent index and whether a ZWNJ blocks it."""
    cursor = index + step
    saw_zwj = False
    while 0 <= cursor < len(chars):
        char = chars[cursor]
        if char == _ZWNJ:
            return None, True
        if char == _ZWJ:
            saw_zwj = True
            cursor += step
            continue
        if _transparent(char):
            cursor += step
            continue
        return cursor, False
    return None, False


def reshape_arabic(text: str) -> str:
    """Replace Arabic base letters with their contextual presentation forms."""
    chars = list(str(text))
    result: list[str] = []
    forms = _forms()
    for index, char in enumerate(chars):
        if char in {_ZWJ, _ZWNJ}:
            continue
        data = forms.get(char)
        if not data:
            result.append(char)
            continue
        prev_index, prev_blocked = _neighbor(chars, index, -1)
        next_index, next_blocked = _neighbor(chars, index, 1)
        join_prev = (
            not prev_blocked
            and prev_index is not None
            and _can_join_next(chars[prev_index])
            and _can_join_previous(char)
        )
        join_next = (
            not next_blocked
            and next_index is not None
            and _can_join_next(char)
            and _can_join_previous(chars[next_index])
        )
        if join_prev and join_next and "medial" in data:
            form = "medial"
        elif join_prev and "final" in data:
            form = "final"
        elif join_next and "initial" in data:
            form = "initial"
        else:
            form = "isolated"
        result.append(data.get(form) or data.get("isolated") or char)
    return "".join(result)


def _clusters(text: str) -> list[str]:
    clusters: list[str] = []
    for char in text:
        if _transparent(char) and clusters:
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def _reverse_arabic_token(token: str) -> str:
    shaped = reshape_arabic(token)
    return "".join(reversed(_clusters(shaped))).translate(_MIRROR)


def visual_rtl(text: str) -> str:
    """Convert logical Arabic text into visual order for LTR-only renderers.

    The report renderer normalizes whitespace before this function is called.
    Whole word tokens are reversed for an RTL paragraph; Arabic tokens are also
    reversed by grapheme cluster after contextual shaping, while Latin and
    numeric tokens preserve their internal order.
    """
    value = str(text)
    if not contains_arabic(value):
        return value
    output_lines: list[str] = []
    for line in value.split("\n"):
        tokens = re.findall(r"\S+", line)
        visual_tokens: list[str] = []
        for token in reversed(tokens):
            visual_tokens.append(_reverse_arabic_token(token) if contains_arabic(token) else token)
        output_lines.append(" ".join(visual_tokens))
    return "\n".join(output_lines)
