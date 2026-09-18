"""Sanitisation of untrusted text before it reaches a terminal or log.

Commit messages and trailers are attacker-controlled. Printing them verbatim
allows terminal escape injection, e.g. an ANSI "erase line" sequence that hides
``Co-authored-by: <AI agent>`` from a reviewer while it still exists in the
commit. Sanitised output makes every such byte visible instead of executing it.
"""

DEFAULT_MAX_LENGTH = 500
_TRUNCATION_MARKER = "...[truncated]"

# Bidirectional overrides can visually reorder text ("Trojan Source").
_BIDI_CONTROLS = frozenset(
    chr(code)
    for code in (
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    )
)


def _escape_char(char: str) -> str:
    code = ord(char)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}"


def sanitize_for_terminal(
    text: str,
    *,
    max_length: int = DEFAULT_MAX_LENGTH,
    keep_newlines: bool = False,
) -> str:
    """Return ``text`` made safe to print to a terminal or plain-text log.

    * C0/C1 control characters (including ESC, so no ANSI sequence can take
      effect) and Unicode bidi controls are rendered as visible escapes.
    * Tabs become spaces; newlines are escaped unless ``keep_newlines`` is set.
    * The result is truncated to at most ``max_length`` characters.
    """
    if max_length < len(_TRUNCATION_MARKER):
        raise ValueError(f"max_length must be at least {len(_TRUNCATION_MARKER)}")

    out: list[str] = []
    for char in text:
        code = ord(char)
        if char == "\n" and keep_newlines:
            out.append(char)
        elif char == "\t":
            out.append(" ")
        elif code < 0x20 or 0x7F <= code <= 0x9F or char in _BIDI_CONTROLS:
            out.append(_escape_char(char))
        else:
            out.append(char)
    result = "".join(out)

    if len(result) > max_length:
        result = result[: max_length - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    return result


def sanitize_block(text: str, *, max_length: int = DEFAULT_MAX_LENGTH, indent: str = "  ") -> str:
    """Sanitise multi-line text for a human-readable report block.

    Newlines are kept - a validation error that lists several fields is
    unreadable once they become ``\\x0a`` - but every line after the first is
    indented, so text we did not write cannot produce a line that starts at
    column 0 and impersonates one of ours (for example a forged ``Result:
    PASS``). Everything :func:`sanitize_for_terminal` escapes is still escaped.
    """
    safe = sanitize_for_terminal(text, max_length=max_length, keep_newlines=True)
    first, separator, rest = safe.partition("\n")
    if not separator:
        return first
    return first + "\n" + "\n".join(indent + line for line in rest.split("\n"))
