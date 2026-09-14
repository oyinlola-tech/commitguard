"""Deterministic normalisation of untrusted identity text for *matching*.

Normalised values are only ever used as comparison keys. Evidence shown to
users always keeps the original (sanitised) text.

What is normalised, and why:

* Unicode NFKC - fullwidth / mathematical letters (``𝐂𝐥𝐚𝐮𝐝𝐞``) fold to ASCII;
* control (Cc) and format (Cf) characters removed - zero-width spaces, bidi
  overrides and escape codes cannot split a name to evade matching;
* case folding and whitespace collapsing;
* a *small, explicit* map of Cyrillic/Greek letters that are visually
  identical to Latin letters (``Clаude`` with a Cyrillic ``а``).

What is deliberately **not** done: removing punctuation, stemming, substring
or fuzzy matching. ``Claude`` must never match ``Claudette``.
"""

import unicodedata

# Visually identical to Latin letters in common fonts. Lowercase only: input is
# case-folded first. Keep this list conservative; every entry is a potential
# false-positive source for legitimate non-Latin names.
_HOMOGLYPHS = str.maketrans(
    {
        # Cyrillic
        "а": "a",
        "в": "b",
        "е": "e",
        "һ": "h",
        "і": "i",
        "ј": "j",
        "к": "k",
        "м": "m",
        "о": "o",
        "р": "p",
        "с": "c",
        "ѕ": "s",
        "т": "t",
        "у": "y",
        "х": "x",
        "ԁ": "d",
        "ԛ": "q",
        "ԝ": "w",
        # Greek
        "α": "a",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "ν": "v",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "υ": "u",
        "χ": "x",
        # Latin look-alikes outside ASCII
        "ı": "i",
        "ȷ": "j",
    }
)


def _strip_invisible(text: str) -> str:
    # Whitespace controls (tab, newline, ...) are kept so they still separate words.
    return "".join(
        ch for ch in text if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def normalize_text(text: str) -> str:
    """Return a case-, width- and whitespace-insensitive comparison key."""
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
