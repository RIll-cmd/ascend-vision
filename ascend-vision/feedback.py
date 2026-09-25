"""Focus-only metadata feedback; the worker never receives video or SQLite handles."""
from dataclasses import dataclass
import json
import logging
import math
import os
import queue
import re
import threading
import time
from typing import Optional, Callable

from speech import OfflineSpeaker, SpeechOutcome

LOG = logging.getLogger(__name__)

INSTRUCTIONS_BASE = (
    'You are a sharp-witted driver and focus safety coach reacting to a confirmed distraction or fatigue event during a session. '
    'Keep your response strictly under 30 words. Never attack worth, identity, or appearance. Return only the line to speak.'
)

PROMPT_STRATEGIES = {
    'drowsiness_microsleep': (
        'CRITICAL SAFETY ALERT: The user has their eyes closed and is experiencing microsleep/drowsiness. '
        'Deliver a loud, sharp, urgent wake-up command commanding them to open their eyes immediately and stay awake or pull over. '
        'Be commanding, direct, and under 15 words.'
    ),
    'yawn': (
        'FATIGUE COACH: The user just completed a prolonged yawn. '
        'Deliver a sharp, witty, energetic remark teasing their lack of sleep and advising a cup of coffee, water, or a stretch break. '
        'Be playful yet clear. Under 25 words.'
    ),
    'phone_held': (
        'DISTRACTION ROASTER: The user picked up or is holding their cell phone during focus time. '
        'Deliver a sarcastic, witty one-sentence roast teasing the distraction. '
        'If posture is "texting", tease typing under the desk. If posture is "call", tease taking calls on duty. '
        'Keep under 25 words.'
    ),
    'slouch': (
        'POSTURE COACH: The user is slouching with bad posture or forward head posture. '
        'Deliver a sarcastic, witty one-sentence roast commanding them to straighten their back, uncurl their spine, and sit tall. '
        'Keep under 25 words.'
    ),
    'poor_posture': (
        'POSTURE COACH: The user is slouching with bad posture or forward head posture. '
        'Deliver a sarcastic, witty one-sentence roast commanding them to straighten their back, uncurl their spine, and sit tall. '
        'Keep under 25 words.'
    ),
}

INSTRUCTIONS = f"{INSTRUCTIONS_BASE} {PROMPT_STRATEGIES['phone_held']}"


def get_instruction(context: 'RoastContext') -> str:
    strategy = PROMPT_STRATEGIES.get(context.event_type, PROMPT_STRATEGIES['phone_held'])
    return f"{INSTRUCTIONS_BASE} {strategy}"


FALLBACK_ALERTS = {
    'drowsiness_microsleep': [
        'Wake up! Open your eyes and stay alert!',
        'Danger! Eyes on the road, wake up immediately!',
        'Microsleep detected! Pull over and take a rest!'
    ],
    'yawn': [
        'Big yawn detected. Time to grab a coffee or take a quick stretch.',
        'Looks like someone forgot to sleep last night. Stay focused.',
        'Heavy fatigue detected. Do not push yourself too hard.'
    ],
    'slouch': [
        "Straighten your back, you're turning into a shrimp.",
        "Fix that posture before your spine files a formal complaint.",
        "Sit up straight! Your chair has a backrest for a reason.",
        "Spine check! Uncurl yourself and sit tall."
    ],
    'poor_posture': [
        "Straighten your back, you're turning into a shrimp.",
        "Fix that posture before your spine files a formal complaint.",
        "Sit up straight! Your chair has a backrest for a reason.",
        "Spine check! Uncurl yourself and sit tall."
    ],
    'phone_held': [
        'Put the phone down and get back to work.',
        'Your phone can wait. Eyes back on the task.',
        'Screen time is over. Focus on what you are doing.'
    ],
    'phone_held_texting': [
        "Put that phone down, whatever you're typing can wait.",
        'Caught texting under the table. Back to focus.'
    ],
    'phone_held_call': [
        'Finish the phone call and return your attention to work.',
        'Hands off the phone while you are supposed to be focused.'
    ],
}


def get_fallback_alert(context: 'RoastContext') -> str:
    if context.event_type == 'drowsiness_microsleep':
        options = FALLBACK_ALERTS['drowsiness_microsleep']
    elif context.event_type == 'yawn':
        options = FALLBACK_ALERTS['yawn']
    elif context.event_type in ('slouch', 'poor_posture'):
        options = FALLBACK_ALERTS['slouch']
    elif context.event_type == 'phone_held':
        if context.posture == 'texting':
            options = FALLBACK_ALERTS['phone_held_texting']
        elif context.posture == 'call':
            options = FALLBACK_ALERTS['phone_held_call']
        else:
            options = FALLBACK_ALERTS['phone_held']
    else:
        options = FALLBACK_ALERTS['phone_held']
    index = (context.pickups_today + context.pickups_this_session) % len(options)
    return options[index]


FALLBACK_CHAT_REPLIES = [
    "I'm keeping an eye on your focus, so don't try to distract me with chatter!",
    "Less talking and more working, before I have to roast your posture again.",
    "I heard that, but your screen is getting lonely. Eyes back on the prize!",
    "Are you really trying to debate a shark right now? Sit up straight and get back to work!",
    "Posture check! You can chat with me once your work is done.",
]


def get_fallback_chat_reply(user_text: str, context: Optional['ConversationContext'] = None) -> str:
    if context is not None:
        if context.slouch_events > 2:
            return "I'd answer that, but your spine is currently begging for mercy. Sit up!"
        if context.phone_pickups > 2:
            return "You're chatting with me after touching your phone that many times? Back to work!"
        if context.microsleep_events > 0:
            return "Less talking, more waking up! Don't make me sound the alarm again."
    index = abs(hash(user_text)) % len(FALLBACK_CHAT_REPLIES)
    return FALLBACK_CHAT_REPLIES[index]


from feedback_router import (
    USER_NAME,
    EXPRESSION_PROMPT_STRATEGIES,
    FALLBACK_EXPRESSION_REPLIES,
    get_expression_prompt_context,
    get_fallback_expression_reply,
)


class OfflineRoaster:
    """Offline safety alert generator for when LLM API is unavailable or disabled."""
    def __init__(self, config=None):
        self.config = config

    def generate(self, context: 'RoastContext') -> str:
        return get_fallback_alert(context)

    def generate_chat(self, user_text: str, context: Optional['ConversationContext'] = None, max_words: int = 25) -> str:
        return get_fallback_chat_reply(user_text, context)

    def generate_expression(self, emotion: str) -> str:
        return get_fallback_expression_reply(emotion)

    def close(self):
        pass


@dataclass(frozen=True)
class ConversationContext:
    user_query: str
    mode: str = 'background'
    phone_pickups: int = 0
    microsleep_events: int = 0
    yawns: int = 0
    slouch_events: int = 0
    session_duration_minutes: float = 0.0

    def payload(self) -> dict:
        return {
            'user_query': self.user_query,
            'mode': self.mode,
            'phone_pickups': self.phone_pickups,
            'microsleep_events': self.microsleep_events,
            'yawns': self.yawns,
            'slouch_events': self.slouch_events,
            'session_duration_minutes': round(self.session_duration_minutes, 1)
        }




@dataclass(frozen=True)
class RoastContext:
    pickups_today: int
    pickups_this_session: int
    session_duration_minutes: float
    time_of_day: str
    event_type: str = 'phone_held'
    posture: str = 'none'

    def __post_init__(self):
        for value in (self.pickups_today, self.pickups_this_session):
            if type(value) is not int or value < 0:
                raise ValueError('Pickup counts must be nonnegative integers')
        if (type(self.session_duration_minutes) not in (int, float)
                or not math.isfinite(self.session_duration_minutes) or self.session_duration_minutes < 0):
            raise ValueError('Session duration must be finite and nonnegative')
        if not isinstance(self.time_of_day, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', self.time_of_day):
            raise ValueError('Time of day must be HH:MM')
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError('event_type must be a nonempty string')
        if not isinstance(self.posture, str):
            raise ValueError('posture must be a string')

    def payload(self):
        # Explicit allowlist: later additions to application objects cannot leak frames.
        data = {'pickups_today': self.pickups_today, 'pickups_this_session': self.pickups_this_session,
                'session_duration_minutes': self.session_duration_minutes, 'time_of_day': self.time_of_day}
        if self.event_type != 'phone_held':
            data['event_type'] = self.event_type
        if self.posture != 'none':
            data['posture'] = self.posture
        return data



class GeminiRoaster:
    def __init__(self, config, *, client=None):
        self.config = config
        if client is None:
            key = os.environ.get(config.api_key_env, '').strip()
            if not key:
                raise ValueError(f'Set {config.api_key_env} to enable Gemini feedback')
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key, vertexai=False,
                http_options=types.HttpOptions(
                    base_url='https://generativelanguage.googleapis.com',
                    timeout=int(config.request_timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1)))
        self.client = client

    def generate(self, context: RoastContext):
        if type(context) is not RoastContext:
            raise TypeError('Feedback accepts only RoastContext metadata')
        from google.genai import types
        options = types.GenerateContentConfig(system_instruction=get_instruction(context),
            max_output_tokens=self.config.max_output_tokens, response_mime_type='text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        if self.config.model.startswith('gemini-2.5-flash'):
            options.thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif self.config.model.startswith('gemini-3') and 'flash' in self.config.model:
            options.thinking_config = types.ThinkingConfig(thinking_level='minimal')
        response = self.client.models.generate_content(model=self.config.model,
            contents=json.dumps(context.payload(), allow_nan=False), config=options)
        if not response.candidates or response.candidates[0].finish_reason != 'STOP':
            raise ValueError('Incomplete roast response')
        text = ' '.join((response.text or '').split()).strip('"')
        if not text or len(text) > self.config.max_characters or len(text.split()) > 45:
            raise ValueError('Roast response is empty or too long')
        return text

    def generate_chat(self, user_text: str, context: Optional[ConversationContext] = None, max_words: int = 25) -> str:
        if not user_text or not user_text.strip():
            raise ValueError('user_text must be nonempty')
        from google.genai import types
        persona_instruction = (
            f"You are a sharp-tongued, ultra-competent AI diva talking to {USER_NAME} during their work session. "
            f"{USER_NAME} is talking to you during their work or monitoring session. "
            "Reply with witty banter, gentle teasing, or playful coaching directly answering what they said. "
            f"STRICT CONSTRAINTS: strictly 1 to 2 short sentences, under {max_words} words total. "
            "Be snappy and conversational for instant speech synthesis. Output only the spoken sentence, no quotes, asterisks, or metadata."
        )
        ctx_data = {}
        if context is not None:
            ctx_data = context.payload()
        prompt_content = f"User said: \"{user_text.strip()}\"\nRecent session telemetry: {json.dumps(ctx_data)}"
        options = types.GenerateContentConfig(
            system_instruction=persona_instruction,
            max_output_tokens=60,
            response_mime_type='text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        if self.config.model.startswith('gemini-2.5-flash'):
            options.thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif self.config.model.startswith('gemini-3') and 'flash' in self.config.model:
            options.thinking_config = types.ThinkingConfig(thinking_level='minimal')
        response = self.client.models.generate_content(
            model=self.config.model,
            contents=prompt_content,
            config=options
        )
        if not response.candidates or response.candidates[0].finish_reason != 'STOP':
            raise ValueError('Incomplete chat response')
        text = ' '.join((response.text or '').split()).strip('"`*\'')
        words = text.split()
        if len(words) > max_words:
            text = ' '.join(words[:max_words])
        return text

    def generate_expression(self, emotion: str) -> str:
        from google.genai import types
        prompt = get_expression_prompt_context(emotion)
        max_words = 15 if emotion == 'smiling' else 18
        persona_instruction = (
            f"You are a sharp-tongued, ultra-competent AI diva talking to {USER_NAME}. "
            f"Keep responses snappy, sharp, playful, and strictly under {max_words} words total. "
            "Output only the spoken sentence, no quotes, asterisks, or metadata."
        )
        options = types.GenerateContentConfig(
            system_instruction=persona_instruction,
            max_output_tokens=50,
            response_mime_type='text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        if self.config.model.startswith('gemini-2.5-flash'):
            options.thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif self.config.model.startswith('gemini-3') and 'flash' in self.config.model:
            options.thinking_config = types.ThinkingConfig(thinking_level='minimal')
        response = self.client.models.generate_content(
            model=self.config.model,
            contents=prompt,
            config=options
        )
        if not response.candidates or response.candidates[0].finish_reason != 'STOP':
            raise ValueError('Incomplete expression response')
        text = ' '.join((response.text or '').split()).strip('"`*\'')
        words = text.split()
        if len(words) > max_words:
            text = ' '.join(words[:max_words])
        return text

    def close(self):
        self.client.close()


class LLMRoaster:
    """Roaster backed by the dual Groq/Gemini LLMRouter with automatic failover."""
    def __init__(self, config=None, *, router=None):
        self.config = config
        from llm_router import get_router
        self._router = router or get_router()

    def generate(self, context: RoastContext) -> str:
        if type(context) is not RoastContext:
            raise TypeError('Feedback accepts only RoastContext metadata')
        instruction = get_instruction(context)
        payload_str = json.dumps(context.payload(), allow_nan=False)
        max_tokens = getattr(self.config, 'max_output_tokens', 60) if self.config else 60
        text = self._router.generate_response(
            prompt=payload_str,
            system_prompt=instruction,
            task="fast",
            max_tokens=max_tokens
        )
        if not text:
            return get_fallback_alert(context)
        return text

    def generate_chat(self, user_text: str, context: Optional[ConversationContext] = None, max_words: int = 25) -> str:
        if not user_text or not user_text.strip():
            raise ValueError('user_text must be nonempty')
        ctx_data = context.payload() if context is not None else {}
        prompt_content = f"User said: \"{user_text.strip()}\"\nRecent session telemetry: {json.dumps(ctx_data)}"
        persona_instruction = (
            f"You are a sharp-tongued, ultra-competent AI diva talking to {USER_NAME} during their work session. "
            f"{USER_NAME} is talking to you during their work or monitoring session. "
            "Reply with witty banter, gentle teasing, or playful coaching directly answering what they said. "
            f"STRICT CONSTRAINTS: strictly 1 to 2 short sentences, under {max_words} words total. "
            "Be snappy and conversational for instant speech synthesis. Output only the spoken sentence, no quotes, asterisks, or metadata."
        )
        reply = self._router.generate_response(
            prompt=prompt_content,
            system_prompt=persona_instruction,
            task="fast",
            max_tokens=60
        )
        if not reply:
            return get_fallback_chat_reply(user_text, context)
        return reply

    def generate_expression(self, emotion: str) -> str:
        prompt = get_expression_prompt_context(emotion)
        max_words = 15 if emotion == 'smiling' else 18
        persona_instruction = (
            f"You are a sharp-tongued, ultra-competent AI diva talking to {USER_NAME}. "
            f"Keep responses snappy, sharp, playful, and strictly under {max_words} words. "
            "Output only the spoken sentence, no quotes, asterisks, or metadata."
        )
        reply = self._router.generate_response(
            prompt=prompt,
            system_prompt=persona_instruction,
            task="fast",
            max_tokens=50
        )
        if not reply:
            return get_fallback_expression_reply(emotion)
        return reply

    def close(self):
        pass


@dataclass(frozen=True)
class FeedbackResult:
    event_id: int
    text: str | None = None
    spoken: bool = False  # speech started; interrupted speech is also recorded
    completed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _Job:
    event_id: int
    session_id: int
    generation: int
    context: RoastContext
    created: float


@dataclass(frozen=True)
class _AnnouncementJob:
    text: str
    created: float


@dataclass(frozen=True)
class _ChatJob:
    user_text: str
    context: Optional[ConversationContext]
    max_words: int
    created: float


@dataclass(frozen=True)
class _ExpressionJob:
    emotion: str
    created: float


class FeedbackService:
    def __init__(self, config, cooldown_seconds, *, generator=None, speaker=None):
        self.config = config
        self.cooldown_seconds = cooldown_seconds
        self._generator = generator
        self._assistant_service = None
        self._speaker = speaker
        self._jobs = queue.Queue(maxsize=8)
        self._results = queue.Queue(maxsize=8)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._session = None
        self._generation = 0
        self._speech_generation = 0
        self._busy = False
        self._chat_busy = False
        self._expression_busy = False
        self._last_attempt = None
        self._last_chat_attempt = None
        self._last_expression_attempt = None
        self._last_expression_by_type = {}
        self._last_event = 0
        self._muted_until = None
        self._is_speaking = False
        self._speech_ended_at: float | None = None
        self.enabled = False

    def bind_assistant(self, assistant_service) -> None:
        """Use the shared answer service for future conversational speech jobs."""
        if not callable(getattr(assistant_service, "respond", None)):
            raise TypeError("assistant_service must provide respond")
        with self._lock:
            self._assistant_service = assistant_service

    def mute(self, duration_seconds: float):
        with self._lock:
            self._muted_until = time.monotonic() + max(1.0, duration_seconds)
        self.cancel_speech()

    def unmute(self):
        with self._lock:
            self._muted_until = None

    def is_muted(self) -> bool:
        with self._lock:
            return self._muted_until is not None and time.monotonic() < self._muted_until

    def is_speaking(self) -> bool:
        """Returns True if the TTS speaker is actively playing audio or within echo-suppression grace window."""
        with self._lock:
            if self._is_speaking:
                return True
            if self._speech_ended_at is not None and (time.monotonic() - self._speech_ended_at) < 0.5:
                return True
            return False

    def cancel_speech(self):
        """Cancel active and queued TTS without stopping Vision or its worker."""
        with self._lock:
            self._speech_generation += 1
            self._chat_busy = False
            self._expression_busy = False
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                return

    def _speech_cancelled(self, generation: int) -> bool:
        with self._lock:
            return (self._stop.is_set() or self._speech_generation != generation
                    or self.is_muted())

    def speak_announcement(self, text: str) -> bool:
        if not text or not self.enabled or self._stop.is_set() or self.is_muted():
            return False
        try:
            self._jobs.put_nowait(_AnnouncementJob(text, time.monotonic()))
            return True
        except queue.Full:
            LOG.warning('Announcement dropped; feedback queue full')
            return False

    def submit_chat(
        self,
        user_text: str,
        context: Optional[ConversationContext] = None,
        max_words: int = 25,
        cooldown: float = 5.0
    ) -> bool:
        """Asynchronously queues an arbitrary user speech utterance for conversational Gemini response and TTS."""
        if not user_text or not user_text.strip() or not self.enabled or self._stop.is_set():
            return False
        if self.is_muted():
            return False
        now = time.monotonic()
        with self._lock:
            if self._chat_busy:
                LOG.debug('Chat dropped; another chat is currently busy')
                return False
            if self._is_speaking:
                LOG.debug('Chat dropped; TTS speaker is currently active')
                return False
            if self._speech_ended_at is not None and (now - self._speech_ended_at) < 0.5:
                LOG.debug('Chat dropped; within echo suppression window')
                return False
            if self._last_chat_attempt is not None and (now - self._last_chat_attempt) < cooldown:
                LOG.debug('Chat dropped; cooldown active')
                return False
            self._chat_busy = True
            self._last_chat_attempt = now
            try:
                self._jobs.put_nowait(_ChatJob(user_text.strip(), context, max_words, now))
                LOG.info('Chat job queued for: "%s"', user_text.strip())
                return True
            except queue.Full:
                self._chat_busy = False
                LOG.warning('Chat job dropped; feedback queue full')
                return False

    def submit_expression(
        self,
        emotion: str,
        cooldown: Optional[float] = None
    ) -> bool:
        """Asynchronously queues an emotion or phone reaction trigger for AI Diva reaction."""
        if not emotion or not emotion.strip() or not self.enabled or self._stop.is_set():
            return False
        if self.is_muted():
            return False
        norm = emotion.strip().lower()
        if cooldown is None:
            cooldown = 60.0 if ('phone' in norm or 'call' in norm or 'scroll' in norm or 'game' in norm) else 45.0
        now = time.monotonic()
        with self._lock:
            if self._expression_busy:
                LOG.debug('Reaction dropped; another reaction is currently busy')
                return False
            if self._is_speaking:
                LOG.debug('Reaction dropped; TTS speaker is currently active')
                return False
            if self._speech_ended_at is not None and (now - self._speech_ended_at) < 0.5:
                LOG.debug('Reaction dropped; within echo suppression window')
                return False
            last_attempt = self._last_expression_by_type.get(norm, self._last_expression_attempt)
            if last_attempt is not None and (now - last_attempt) < cooldown:
                LOG.debug('Reaction dropped; cooldown active for %s (%.1fs remaining)', norm, cooldown - (now - last_attempt))
                return False
            self._expression_busy = True
            self._last_expression_attempt = now
            self._last_expression_by_type[norm] = now
            try:
                self._jobs.put_nowait(_ExpressionJob(emotion.strip(), now))
                LOG.info('Reaction job queued for: "%s"', emotion.strip())
                return True
            except queue.Full:
                self._expression_busy = False
                LOG.warning('Reaction job dropped; feedback queue full')
                return False

    submit_trigger = submit_expression

    def start(self):
        if not self.config.enabled:
            return
        has_keys = (
            bool(os.environ.get(self.config.api_key_env, '').strip())
            or bool(os.environ.get('GROQ_API_KEY', '').strip())
            or bool(os.environ.get('CEREBRAS_API_KEY', '').strip())
        )
        if self._generator is None and self._speaker is None and not has_keys:
            LOG.warning('Feedback unavailable: set %s, GROQ_API_KEY, or CEREBRAS_API_KEY. Detection and logging remain active.', self.config.api_key_env)
            return
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError('FeedbackService is single-use')

        self.enabled = True
        self._thread = threading.Thread(target=self._run, name='phone-feedback', daemon=True)
        self._thread.start()

    def set_session(self, focus_session_id):
        with self._lock:
            if focus_session_id != self._session:
                self._generation += 1
                self._session = focus_session_id

    def submit(self, event_id, session_id, context):
        if type(context) is not RoastContext:
            raise TypeError('Feedback accepts only RoastContext metadata')
        if self.is_muted():
            return False
        now = time.monotonic()
        with self._lock:
            if (not self.enabled or self._stop.is_set() or self._session is None
                    or self._session != session_id or self._busy or event_id <= self._last_event
                    or (self._last_attempt is not None and now - self._last_attempt < self.cooldown_seconds)):
                return False
            self._busy = True
            self._last_attempt = now
            self._last_event = event_id
            try:
                self._jobs.put_nowait(_Job(event_id, session_id, self._generation, context, now))
                return True
            except queue.Full:
                self._busy = False
                return False

    def _cancelled(self, job, *, check_age=True):
        with self._lock:
            return (self._stop.is_set() or self._session != job.session_id or self._generation != job.generation
                    or (check_age and time.monotonic() - job.created > self.config.max_age_seconds))

    def _speak_text(self, text: str, cancel_check: Callable[[], bool]) -> SpeechOutcome:
        """Internal helper executing speech through the configured engine while managing speaking state."""
        if self._speaker is None:
            if getattr(self.config, 'tts_engine', 'kokoro_onnx') == 'kokoro_onnx':
                try:
                    from tts_engine import KokoroSpeaker
                    self._speaker = KokoroSpeaker(self.config)
                except Exception as err:
                    LOG.warning('Failed to load KokoroSpeaker (%s), falling back to OfflineSpeaker', err)
                    self._speaker = OfflineSpeaker(self.config)
            else:
                self._speaker = OfflineSpeaker(self.config)

        with self._lock:
            self._is_speaking = True
        try:
            outcome = self._speaker.speak(text, cancel_check)
            if not isinstance(outcome, SpeechOutcome):
                # Accommodate mock objects in unit tests
                outcome = SpeechOutcome(getattr(outcome, 'started', True), getattr(outcome, 'completed', True))
            return outcome
        finally:
            with self._lock:
                self._is_speaking = False
                self._speech_ended_at = time.monotonic()

    def _run(self):
        try:
            # Pre-warm the TTS speaker in background on startup if using kokoro_onnx
            if not self._stop.is_set() and self._speaker is None and getattr(self.config, 'tts_engine', 'kokoro_onnx') == 'kokoro_onnx':
                try:
                    from tts_engine import KokoroSpeaker
                    self._speaker = KokoroSpeaker(self.config)
                    if not self._stop.is_set():
                        self._speaker.warmup()
                except Exception as err:
                    LOG.warning('TTS pre-warming failed or skipped: %s', err)

            while not self._stop.is_set():
                try:
                    job = self._jobs.get(timeout=.05)
                except queue.Empty:
                    continue

                if isinstance(job, _AnnouncementJob):
                    try:
                        with self._lock:
                            speech_generation = self._speech_generation
                        if not self._speech_cancelled(speech_generation):
                            self._speak_text(job.text, lambda: self._speech_cancelled(speech_generation))
                    except Exception as err:
                        LOG.warning('Announcement speech failed: %s', err)
                    continue

                if isinstance(job, _ChatJob):
                    try:
                        with self._lock:
                            speech_generation = self._speech_generation
                        if not self._speech_cancelled(speech_generation):
                            if hasattr(job, 'created') and time.monotonic() - job.created > self.config.max_age_seconds:
                                object.__setattr__(job, 'created', time.monotonic())
                            if self._assistant_service is None and self._generator is None:
                                has_keys = (
                                    bool(os.environ.get(self.config.api_key_env, '').strip())
                                    or bool(os.environ.get('GROQ_API_KEY', '').strip())
                                    or bool(os.environ.get('CEREBRAS_API_KEY', '').strip())
                                )
                                if not has_keys:
                                    self._generator = OfflineRoaster(self.config)
                                else:
                                    self._generator = LLMRoaster(self.config)
                            reply_text = None
                            try:
                                if self._assistant_service is not None:
                                    reply_text = self._assistant_service.respond(
                                        job.user_text, job.context, max_words=job.max_words,
                                    ).text
                                elif hasattr(self._generator, 'generate_chat'):
                                    reply_text = self._generator.generate_chat(job.user_text, job.context, max_words=job.max_words)
                            except Exception as api_err:
                                LOG.warning('Chat request failed (%s); using witty offline banter fallback', type(api_err).__name__)
                                reply_text = get_fallback_chat_reply(job.user_text, job.context)
                            if not reply_text:
                                reply_text = get_fallback_chat_reply(job.user_text, job.context)
                            LOG.info('CONVERSATIONAL_REPLY: "%s"', reply_text)
                            if reply_text and not self._speech_cancelled(speech_generation):
                                self._speak_text(reply_text, lambda: self._speech_cancelled(speech_generation))
                    except Exception as err:
                        LOG.error('Conversational chat generation/speech failed: %s', err)
                    finally:
                        with self._lock:
                            self._chat_busy = False
                    continue

                if isinstance(job, _ExpressionJob):
                    try:
                        with self._lock:
                            speech_generation = self._speech_generation
                        if not self._speech_cancelled(speech_generation):
                            if hasattr(job, 'created') and time.monotonic() - job.created > self.config.max_age_seconds:
                                object.__setattr__(job, 'created', time.monotonic())
                            if self._generator is None:
                                has_keys = (
                                    bool(os.environ.get(self.config.api_key_env, '').strip())
                                    or bool(os.environ.get('GROQ_API_KEY', '').strip())
                                    or bool(os.environ.get('CEREBRAS_API_KEY', '').strip())
                                )
                                if not has_keys:
                                    self._generator = OfflineRoaster(self.config)
                                else:
                                    self._generator = LLMRoaster(self.config)
                            reply_text = None
                            try:
                                if hasattr(self._generator, 'generate_expression'):
                                    reply_text = self._generator.generate_expression(job.emotion)
                            except Exception as api_err:
                                LOG.warning('Expression roast request failed (%s); using fallback line', type(api_err).__name__)
                                reply_text = get_fallback_expression_reply(job.emotion)
                            if not reply_text:
                                reply_text = get_fallback_expression_reply(job.emotion)
                            LOG.info('EXPRESSION_REPLY (%s): "%s"', job.emotion, reply_text)
                            if reply_text and not self._speech_cancelled(speech_generation):
                                self._speak_text(reply_text, lambda: self._speech_cancelled(speech_generation))
                    except Exception as err:
                        LOG.error('Expression reaction generation/speech failed: %s', err)
                    finally:
                        with self._lock:
                            self._expression_busy = False
                    continue

                # If job arrived during initial model pre-warming, update its created timestamp
                # so it isn't discarded for being older than max_age_seconds
                if hasattr(job, 'created') and time.monotonic() - job.created > self.config.max_age_seconds:
                    object.__setattr__(job, 'created', time.monotonic())
                result = FeedbackResult(job.event_id)
                try:
                    if not self._cancelled(job):
                        if self._generator is None:
                            has_keys = (
                                bool(os.environ.get(self.config.api_key_env, '').strip())
                                or bool(os.environ.get('GROQ_API_KEY', '').strip())
                                or bool(os.environ.get('CEREBRAS_API_KEY', '').strip())
                            )
                            if not has_keys:
                                self._generator = OfflineRoaster(self.config)
                            else:
                                self._generator = LLMRoaster(self.config)
                        text = self._generator.generate(job.context)
                        if not self._cancelled(job):
                            with self._lock:
                                speech_generation = self._speech_generation
                            outcome = self._speak_text(
                                text,
                                lambda: (self._cancelled(job, check_age=False)
                                         or self._speech_cancelled(speech_generation)),
                            )
                            result = FeedbackResult(job.event_id, text if outcome.started else None,
                                                    outcome.started, outcome.completed, outcome.error)
                except Exception as exc:
                    # Provider exceptions may contain response bodies: log only the class.
                    result = FeedbackResult(job.event_id, error=type(exc).__name__)
                finally:
                    with self._lock:
                        self._busy = False
                    try:
                        self._results.put_nowait(result)
                    except queue.Full:
                        LOG.warning('Feedback result queue full; result dropped')
        finally:
            for resource in (self._speaker, self._generator):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        LOG.warning('Feedback resource cleanup failed (%s)', type(exc).__name__)

    def drain(self):
        results = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                return results

    def close(self):
        self._stop.set()
        self.set_session(None)
        if self._thread is not None:
            self._thread.join(self.config.shutdown_timeout_seconds)
            if self._thread.is_alive():
                LOG.warning('Feedback worker still waiting for a provider/driver; future speech is cancelled')
