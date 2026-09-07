"""Narrow classifier for explicit, currently supported Ascend commands."""
import re


def is_ascend_command(text: str) -> bool:
    normalized = text.lower().strip()
    return bool(re.search(r'\b(missions?|habits?)\b', normalized) and
                re.search(r'\b(what|show|list|mark|complete|log|today)\b', normalized))
