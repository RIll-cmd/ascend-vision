"""Explicit-user-intent gate for one-shot local screen inspection."""
from __future__ import annotations

import re


_SCREEN_REQUEST = re.compile(
    r"\b(?:look\s+at|inspect|analy[sz]e|read|describe|what(?:'s|\s+is)\s+(?:on|in)|"
    r"tell\s+me\s+what(?:'s|\s+is)\s+(?:on|in))\s+(?:(?:my|this|the)\s+)?(?:screen|desktop|display)\b",
    re.IGNORECASE,
)


def is_screen_inspection_request(text: str) -> bool:
    return isinstance(text, str) and len(text) <= 4_000 and _SCREEN_REQUEST.search(text.strip()) is not None
