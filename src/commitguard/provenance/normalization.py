"""Deterministic normalisation of untrusted identity text for *matching*.

Normalised values are only ever used as comparison keys. Evidence shown to
users always keeps the original (sanitised) text.

What is normalised, and why:

* Unicode NFKC - fullwidth / mathematical bold letters fold to ASCII;
* control (Cc) and format (Cf) characters removed - zero-width spaces, bidi
  overrides and escape codes cannot split a name to evade matching;
* case folding and whitespace collapsing;
* a *small, explicit* map of Cyrillic/Greek letters that are visually
  identical to Latin letters (``Claude`` spelled with a Cyrillic ``a``, U+0430).

What is deliberately **not** done: removing punctuation, stemming, substring
or fuzzy matching. ``Claude`` must never match ``Claudette``.
"""

import unicodedata

# Visually identical to Latin letters in common fonts. Lowercase only: input is
# case-folded first. Keep this list conservative; every entry is a potential
# false-positive source for legitimate non-Latin names.
# Code points (not literal characters) so the source contains no look-alikes.
_HOMOGLYPHS: dict[int, str] = {
    0x0430: "a",  # CYRILLIC SMALL LETTER A
    0x0432: "b",  # CYRILLIC SMALL LETTER VE
    0x0435: "e",  # CYRILLIC SMALL LETTER IE
    0x04BB: "h",  # CYRILLIC SMALL LETTER SHHA
    0x0456: "i",  # CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
    0x0458: "j",  # CYRILLIC SMALL LETTER JE
    0x043A: "k",  # CYRILLIC SMALL LETTER KA
    0x043C: "m",  # CYRILLIC SMALL LETTER EM
    0x043E: "o",  # CYRILLIC SMALL LETTER O
    0x0440: "p",  # CYRILLIC SMALL LETTER ER
    0x0441: "c",  # CYRILLIC SMALL LETTER ES
    0x0455: "s",  # CYRILLIC SMALL LETTER DZE
    0x0442: "t",  # CYRILLIC SMALL LETTER TE
    0x0443: "y",  # CYRILLIC SMALL LETTER U
    0x0445: "x",  # CYRILLIC SMALL LETTER HA
    0x0501: "d",  # CYRILLIC SMALL LETTER KOMI DE
    0x051B: "q",  # CYRILLIC SMALL LETTER QA
    0x051D: "w",  # CYRILLIC SMALL LETTER WE
    0x03B1: "a",  # GREEK SMALL LETTER ALPHA
    0x03B5: "e",  # GREEK SMALL LETTER EPSILON
    0x03B9: "i",  # GREEK SMALL LETTER IOTA
    0x03BA: "k",  # GREEK SMALL LETTER KAPPA
    0x03BD: "v",  # GREEK SMALL LETTER NU
    0x03BF: "o",  # GREEK SMALL LETTER OMICRON
    0x03C1: "p",  # GREEK SMALL LETTER RHO
    0x03C4: "t",  # GREEK SMALL LETTER TAU
    0x03C5: "u",  # GREEK SMALL LETTER UPSILON
    0x03C7: "x",  # GREEK SMALL LETTER CHI
    0x0131: "i",  # LATIN SMALL LETTER DOTLESS I
    0x0237: "j",  # LATIN SMALL LETTER DOTLESS J
}


# ASCII control characters that are not whitespace: the only Cc/Cf characters ASCII has.
_ASCII_INVISIBLE = {code: None for code in (*range(0x00, 0x20), 0x7F) if not chr(code).isspace()}


def _strip_invisible(text: str) -> str:
    # Whitespace controls (tab, newline, ...) are kept so they still separate words.
    if text.isascii():
        # Fast path, same result: ASCII has no format (Cf) characters. Measured by the
        # performance benchmark: per-character categorisation dominated large messages.
        return text if text.isprintable() else text.translate(_ASCII_INVISIBLE)
    return "".join(
        ch for ch in text if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def normalize_text(text: str) -> str:
    """Return a case-, width- and whitespace-insensitive comparison key."""
    if text.isascii():
        text = _strip_invisible(text)  # NFKC leaves ASCII unchanged
    else:
        text = _strip_invisible(unicodedata.normalize("NFKC", _strip_invisible(text)))
    text = text.casefold().translate(_HOMOGLYPHS)
    return " ".join(text.split())


def normalize_name(name: str) -> str:
    """Normalise a person/agent display name for exact alias comparison."""
    return normalize_text(name)


def name_tokens(name: str) -> tuple[str, ...]:
    """Whitespace tokens of a normalised name (for leading-token prefix rules)."""
    return tuple(normalize_name(name).split())


def normalize_email(email: str) -> str:
    """Normalise an email address for comparison.

    Case-folded as a whole (local parts are case-sensitive in theory, never in
    practice for the providers relevant here); a trailing dot on the domain is
    removed. No other rewriting (e.g. dot removal) is performed.
    """
    email = normalize_text(email).replace(" ", "")
    local, sep, domain = email.rpartition("@")
    if not sep:
        return email
    return f"{local}@{domain.rstrip('.')}"


def normalize_domain(domain: str) -> str:
    return normalize_text(domain).replace(" ", "").strip(".")


def normalize_trailer_key(key: str) -> str:
    """Canonical trailer key: ``Co_Authored By`` -> ``co-authored-by``."""
    text = normalize_text(key).replace("_", " ").replace("-", " ")
    return "-".join(text.split())


def uses_disguising_characters(text: str) -> bool:
    """True if matching ``text`` relied on folding look-alike or invisible characters.

    Plain case and whitespace differences do not count; NFKC compatibility
    forms, control/format characters and homoglyphs do.
    """
    return normalize_text(text) != " ".join(text.casefold().split())
