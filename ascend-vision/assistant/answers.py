"""Short spoken or typed answers to a question Vision asked: yes, not now, and the words they are read from."""

from __future__ import annotations

import re

# A yes starts the answer ("sounds good", "yes let's try it"); any negation later turns it down.
_YES = re.compile(r"(?:(?:hey|ok|okay) )?(?:vision )?(?:yes|yeah|yep|sure|okay|ok|alright|sounds good|"
                  r"let's do it|let's try it|go ahead)\b")
_NEGATION = re.compile(r"\b(?:no|not|don't|never|later|nope)\b")
_NOT_NOW = re.compile(r'(?:(?:hey|ok|okay) )?(?:vision )?(?:not now|no|nope|no thanks|not today)')


def words(text: str) -> str:
    return ' '.join(re.sub(r"[^a-z' ]", ' ', text.lower()).split())


def is_yes(text: str) -> bool:
    said = words(text)
    return bool(_YES.match(said)) and not _NEGATION.search(said)


def is_not_now(text: str) -> bool:
    return bool(_NOT_NOW.fullmatch(words(text)))
