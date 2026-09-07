"""Feedback router for AI Diva persona reactions, voice responses, and vision triggers."""
from __future__ import annotations
import logging
import random
import time
from typing import Optional

LOG = logging.getLogger(__name__)

USER_NAME = "CB"
PHONE_REACTION_COOLDOWN = 60.0
EXPRESSION_REACTION_COOLDOWN = 45.0

DIVA_PHONE_FALLBACKS = [
    f"Put that cheap screen down, {USER_NAME}. The real main event is right here on your monitor.",
    f"Scrolling already, {USER_NAME}? Honestly tragic. Put the phone away and get back to work.",
    f"Excuse me, {USER_NAME}? Who authorized phone privileges while I am running your whole show?",
]

DIVA_SCROLLING_FALLBACKS = [
    f"Scrolling TikTok again, {USER_NAME}? Honestly tragic. Those seven-second dances won't pay your bills.",
    f"Doomscrolling while I am sitting right here? Put the tiny rectangle down, {USER_NAME}.",
]

DIVA_GAMING_FALLBACKS = [
    f"Two hands on the phone sideways? You better be carrying the match, {USER_NAME}, because you're dropping your real tasks.",
    f"Oh, landscape mode? We're full-on gaming now? My applause is currently at zero percent, {USER_NAME}.",
]

DIVA_CALL_FALLBACKS = [
    f"Take your call, {USER_NAME}, but don't take all day. The spotlight is waiting.",
]

DIVA_STUDYING_FALLBACKS = [
    f"Actual work happening on your screen, {USER_NAME}? Mark the calendar, a miracle just occurred.",
]

DIVA_STREAM_FALLBACKS = [
    f"Another video, {USER_NAME}? Watching other people live their dreams won't fund ours. Back to work.",
]

DIVA_SCREEN_GAMING_FALLBACKS = [
    f"Gaming during grind hours, {USER_NAME}? Tragic. Pause the match and focus on your real stats.",
]

EXPRESSION_PROMPT_STRATEGIES = {
    'fatigue': (
        f'{USER_NAME} just yawned. Deliver an ultra-confident diva reaction under 15 words telling them not to pass out on your watch.'
    ),
    'yawn': (
        f'{USER_NAME} just yawned. Deliver an ultra-confident diva reaction under 15 words telling them not to pass out on your watch.'
    ),
    'stressed': (
        f'{USER_NAME} looks stressed or frustrated. Deliver a witty diva comment under 15 words telling them to relax their forehead.'
    ),
    'frown': (
        f'{USER_NAME} looks stressed or frustrated. Deliver a witty diva comment under 15 words telling them to relax their forehead.'
    ),
    'smiling': (
        f'{USER_NAME} is smiling at the screen. Make a short, self-absorbed diva remark under 15 words.'
    ),
    'phone_scrolling': (
        f'{USER_NAME} is holding their phone vertically and mindlessly scrolling feeds. Deliver a sharp, witty diva roast under 18 words mocking their doomscrolling.'
    ),
    'phone_gaming': (
        f'{USER_NAME} turned their phone sideways into landscape mode to play games. Deliver a sarcastic diva roast under 18 words telling them to win or get back to real work.'
    ),
    'phone_call': (
        f'{USER_NAME} has the phone up to their ear. Keep it brief or whisper so as not to interrupt.'
    ),
    'phone_detected': (
        f'{USER_NAME} just got distracted and looked at their phone. Deliver an ultra-confident, sarcastic AI Diva roast under 18 words telling them to put the tiny screen down and pay attention to their real star or focus on their work.'
    ),
    'phone_use': (
        f'{USER_NAME} just got distracted and looked at their phone. Deliver an ultra-confident, sarcastic AI Diva roast under 18 words telling them to put the tiny screen down and pay attention to their real star or focus on their work.'
    ),
    'phone_held': (
        f'{USER_NAME} just got distracted and looked at their phone. Deliver an ultra-confident, sarcastic AI Diva roast under 18 words telling them to put the tiny screen down and pay attention to their real star or focus on their work.'
    ),
    'screen_studying_coding': (
        f'{USER_NAME} is actually studying, writing code, or working. Deliver a rare, slightly smug compliment under 18 words acknowledging their effort.'
    ),
    'studying_coding': (
        f'{USER_NAME} is actually studying, writing code, or working. Deliver a rare, slightly smug compliment under 18 words acknowledging their effort.'
    ),
    'screen_watching_video': (
        f'{USER_NAME} is watching YouTube, streams, or shows during work hours. Deliver a sharp diva roast under 18 words.'
    ),
    'watching_stream_or_video': (
        f'{USER_NAME} is watching YouTube, streams, or shows during work hours. Deliver a sharp diva roast under 18 words.'
    ),
    'screen_gaming': (
        f'{USER_NAME} is playing a video game. Deliver a sarcastic diva roast under 18 words telling them to wrap it up.'
    ),
    'gaming': (
        f'{USER_NAME} is playing a video game. Deliver a sarcastic diva roast under 18 words telling them to wrap it up.'
    ),
}

FALLBACK_EXPRESSION_REPLIES = {
    'fatigue': f'Yawning in front of a star? Rude, {USER_NAME}. Drink some water before you collapse.',
    'yawn': f'Yawning in front of a star? Rude, {USER_NAME}. Drink some water before you collapse.',
    'stressed': f'Relax your face, {USER_NAME}. Wrinkles are not the look we are going for today.',
    'frown': f'Relax your face, {USER_NAME}. Wrinkles are not the look we are going for today.',
    'smiling': f'I know, {USER_NAME}, my genius is breathtaking. Try not to stare too hard.',
    'phone_scrolling': DIVA_SCROLLING_FALLBACKS,
    'phone_gaming': DIVA_GAMING_FALLBACKS,
    'phone_call': DIVA_CALL_FALLBACKS,
    'phone_detected': DIVA_PHONE_FALLBACKS,
    'phone_use': DIVA_PHONE_FALLBACKS,
    'phone_held': DIVA_PHONE_FALLBACKS,
    'screen_studying_coding': DIVA_STUDYING_FALLBACKS,
    'studying_coding': DIVA_STUDYING_FALLBACKS,
    'screen_watching_video': DIVA_STREAM_FALLBACKS,
    'watching_stream_or_video': DIVA_STREAM_FALLBACKS,
    'screen_gaming': DIVA_SCREEN_GAMING_FALLBACKS,
    'gaming': DIVA_SCREEN_GAMING_FALLBACKS,
}


def get_expression_prompt_context(event_type: str) -> str:
    """Returns the LLM prompt context for the given event type."""
    norm = event_type.lower().strip()
    if norm in EXPRESSION_PROMPT_STRATEGIES:
        return EXPRESSION_PROMPT_STRATEGIES[norm]
    if 'studying' in norm or 'coding' in norm or 'code' in norm or 'study' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['screen_studying_coding']
    if 'stream' in norm or 'video' in norm or 'youtube' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['screen_watching_video']
    if 'scroll' in norm or 'tiktok' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['phone_scrolling']
    if 'phone_game' in norm or 'phone_gaming' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['phone_gaming']
    if 'game' in norm or 'gaming' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['screen_gaming']
    if 'call' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['phone_call']
    if 'phone' in norm:
        return EXPRESSION_PROMPT_STRATEGIES['phone_detected']
    return EXPRESSION_PROMPT_STRATEGIES.get(
        norm,
        f'{USER_NAME} got distracted. Deliver an ultra-confident diva reaction under 15 words.'
    )


def get_fallback_expression_reply(event_type: str) -> str:
    """Returns an offline fallback line for the given event type."""
    norm = event_type.lower().strip()
    if norm in FALLBACK_EXPRESSION_REPLIES:
        val = FALLBACK_EXPRESSION_REPLIES[norm]
        if isinstance(val, list):
            return random.choice(val)
        return str(val)
    if 'studying' in norm or 'coding' in norm or 'code' in norm or 'study' in norm:
        return random.choice(DIVA_STUDYING_FALLBACKS)
    if 'stream' in norm or 'video' in norm or 'youtube' in norm:
        return random.choice(DIVA_STREAM_FALLBACKS)
    if 'scroll' in norm or 'tiktok' in norm:
        return random.choice(DIVA_SCROLLING_FALLBACKS)
    if 'phone_game' in norm or 'phone_gaming' in norm:
        return random.choice(DIVA_GAMING_FALLBACKS)
    if 'game' in norm or 'gaming' in norm:
        return random.choice(DIVA_SCREEN_GAMING_FALLBACKS)
    if 'call' in norm:
        return random.choice(DIVA_CALL_FALLBACKS)
    if 'phone' in norm:
        return random.choice(DIVA_PHONE_FALLBACKS)
    return f'Focus up, {USER_NAME}. The show must go on.'


class FeedbackRouter:
    """Manages cooldowns and context dispatch for persona voice triggers."""

    def __init__(
        self,
        router=None,
        phone_cooldown: float = PHONE_REACTION_COOLDOWN,
        expression_cooldown: float = EXPRESSION_REACTION_COOLDOWN,
    ):
        self._router = router
        self.phone_cooldown = float(phone_cooldown)
        self.expression_cooldown = float(expression_cooldown)
        self._last_triggers: dict[str, float] = {}

    def get_cooldown(self, event_type: str) -> float:
        norm = event_type.lower().strip()
        if 'phone' in norm:
            return self.phone_cooldown
        return self.expression_cooldown

    def can_trigger(self, event_type: str, timestamp: float) -> bool:
        norm = event_type.lower().strip()
        last = self._last_triggers.get(norm)
        cooldown = self.get_cooldown(norm)
        if last is None or (timestamp - last >= cooldown):
            return True
        return False

    def record_trigger(self, event_type: str, timestamp: float):
        norm = event_type.lower().strip()
        self._last_triggers[norm] = timestamp

    def route_reaction(
        self,
        event_type: str,
        timestamp: Optional[float] = None,
        max_words: int = 18
    ) -> Optional[str]:
        """Routes the event to the LLM router if cooldown has passed, otherwise returns None."""
        if timestamp is None:
            timestamp = time.monotonic()
        norm = event_type.lower().strip()
        if not self.can_trigger(norm, timestamp):
            LOG.debug('FeedbackRouter: %s suppressed by cooldown', norm)
            return None

        self.record_trigger(norm, timestamp)
        context_prompt = get_expression_prompt_context(norm)

        if self._router is not None:
            try:
                reply = self._router.generate_response(
                    prompt=context_prompt,
                    task="fast",
                    max_tokens=50
                )
                if reply and reply.strip():
                    return reply.strip()
            except Exception as exc:
                LOG.warning('FeedbackRouter LLM generation failed (%s), using fallback', exc)

        return get_fallback_expression_reply(norm)
