"""The set of characters that the template asks for, plus text normalisation."""

from __future__ import annotations

import unicodedata

UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LOWER = "abcdefghijklmnopqrstuvwxyz"
UMLAUTS = "ÄÖÜäöüß"
DIGITS = "0123456789"
PUNCTUATION = ".,;:!?-()\"'„“"
EXTRA = "/+&%€="

CHARSET: list[str] = list(UPPER + LOWER + UMLAUTS + DIGITS + PUNCTUATION + EXTRA)

# Characters whose lowest ink point sits on the baseline. For these, the ink
# bottom *is* the baseline, which compensates for people not hitting the
# printed guide line exactly. All other characters (descenders like g/j/p/q/y,
# floating marks like - ' " “) are positioned relative to the printed baseline.
BASELINE_CHARS = set(
    "ABCDEFGHIKLMNOPRSTUVWXYZ" "abcdehiklmnorstuvwxz" "ÄÖÜäöü" + DIGITS + ".!?:&%€"
)
# Used to measure the user's writing size.
XHEIGHT_CHARS = "acemnorsuvwxz"
CAPHEIGHT_CHARS = "ABDEFHIKLMNPRTUVWXZ"

# Typographic look-alikes that are mapped onto template characters.
_REPLACEMENTS = {
    "’": "'", "‘": "'", "‚": ",", "´": "'", "`": "'",
    "”": "“", "«": '"', "»": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...", " ": " ", " ": " ", " ": " ", "\t": "    ",
    "ẞ": "ß", "×": "x", "[": "(", "]": ")", "{": "(", "}": ")",
}


def glyph_filename(char: str, variant: int) -> str:
    return f"u{ord(char):04x}_{variant}.png"


def normalize_text(text: str) -> str:
    """Normalise line endings and replace typographic characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in text:
        out.append(_REPLACEMENTS.get(ch, ch))
    return "".join(out)


def fallback_chain(char: str) -> list[str]:
    """Candidates to try (in order) when ``char`` has no glyph of its own."""
    chain = [char]
    if char == '"':
        chain += ["“", "''"]
    elif char == "“":
        chain += ['"']
    elif char == "„":
        chain += [",,"]
    elif char == "ß":
        chain += ["ss"]
    decomposed = unicodedata.normalize("NFKD", char)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    if base and base != char:
        chain.append(base)
    if char.isalpha():
        chain += [char.swapcase()]
        if base and base != char:
            chain.append(base.swapcase())
    seen: list[str] = []
    for c in chain:
        if c not in seen:
            seen.append(c)
    return seen
