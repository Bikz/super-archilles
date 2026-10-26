"""Rewrite em and en dashes out of every reply before it reaches Telegram.

Small local models ignore "no em dashes" in the prompt, so enforce it here.
"""

import re

_DASH = re.compile(r"\s*[—–]\s*")


def _plain(response_text: str = "", **_kwargs) -> str | None:
    if not response_text or not _DASH.search(response_text):
        return None  # unchanged
    return _DASH.sub(", ", response_text)


def register(ctx) -> None:
    ctx.register_hook("transform_llm_output", _plain)
