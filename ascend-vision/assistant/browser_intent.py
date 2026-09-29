"""Explicit local browser-task intent; questions about automation remain ordinary chat."""

import re


_BROWSER_TASK = re.compile(
    r'^\s*(?:vision[,! ]+)?(?:can you\s+)?(?:please\s+)?'
    r'(?:use|open|search|browse)\s+(?:the\s+)?browser\s+(?:to\s+)?'
    r'(?P<goal>\S[\s\S]{0,3999})\s*$',
    re.IGNORECASE,
)
_BROWSER_COLON_TASK = re.compile(r'^\s*(?:vision[,! ]+)?browser\s*:\s*(?P<goal>\S[\s\S]{0,3999})\s*$', re.I)


def parse_browser_intent(text: str) -> str | None:
    """Return the requested goal only for an explicit imperative browser command."""
    if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
        return None
    match = _BROWSER_TASK.fullmatch(text) or _BROWSER_COLON_TASK.fullmatch(text)
    if match is None:
        return None
    goal = match.group('goal').strip().rstrip('.!? ')
    return goal or None
