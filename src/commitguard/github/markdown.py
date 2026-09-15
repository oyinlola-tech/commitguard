"""Escaping of untrusted text for GitHub-rendered Markdown (job summaries, Check Runs)."""

from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.secrets import redact


def escape_markdown(value: str, limit: int = 200) -> str:
    """Escape untrusted text for a Markdown paragraph or table cell.

    Control and bidi characters are made visible, credential-shaped strings
    are redacted, Markdown syntax is backslash-escaped (links, images, emphasis,
    headings cannot be injected) and HTML is entity-encoded.
    """
    text = sanitize_for_terminal(redact(value), max_length=limit)
    for char in "\\`*_{}[]()#+-.!~":  # Markdown first: entities below contain '#'
        text = text.replace(char, "\\" + char)
    for char, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("|", "&#124;")):
        text = text.replace(char, entity)
    return text
