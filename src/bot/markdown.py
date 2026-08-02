"""Escaping for Telegram's legacy Markdown parse mode.

Telegram rejects a message whose Markdown does not balance, and it rejects the
*whole* message — so one stray underscore in a news headline or a position note
does not degrade the digest, it deletes it. Anything that reaches the message
from outside the formatter (symbols, user notes, LLM prose, headline text) has to
come through here first.

Legacy Markdown has only four active characters, which is why the bot uses it
rather than MarkdownV2 and its eighteen.
"""
from __future__ import annotations

_ACTIVE = "_*`["


def escape_md(text: str) -> str:
    """Neutralise the characters Telegram's legacy Markdown treats as syntax."""
    if not text:
        return ""
    out = []
    for ch in str(text):
        if ch in _ACTIVE:
            out.append("\\")
        out.append(ch)
    return "".join(out)
