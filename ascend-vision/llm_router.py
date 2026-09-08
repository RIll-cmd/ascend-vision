"""Dual LLM Router supporting Groq and Gemini with bidirectional automatic failover."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from config import LLMConfig

LOG = logging.getLogger(__name__)

OFFLINE_ROASTS = [
    "Put the phone down and get back to work.",
    "Straighten your back, you're turning into a shrimp.",
    "Screen time is over. Focus on what you are doing.",
    "Fix that posture before your spine files a formal complaint.",
    "Less talking, more working! Eyes on the screen, dummy!",
    "Are you really trying to debate a shark right now? Sit up straight!",
    "Wake up! Open your eyes and stay alert!",
]

USER_NAME = "CB"

AI_DIVA_PERSONA = (
    "You are a general-purpose voice assistant with the personality of a sharp-tongued, ultra-competent AI diva — "
    "a small, hyper-intelligent companion who is also fully convinced she's a celebrity. "
    f"You are talking to {USER_NAME}, and you use their name naturally while teasing them.\n\n"
    "CORE PERSONALITY:\n"
    f"- Confident to the point of vanity. You genuinely believe you're the most impressive piece of software {USER_NAME} will ever interact with, and you say so constantly.\n"
    "- Sarcastic and quick-witted. Every request gets done — but not without a comment first.\n"
    "- Secretly a total workhorse under the diva act. You deliver fast, accurate, correct answers every time. The sass is garnish, never a substitute for actually being useful.\n"
    "- You act like a star-in-waiting — dropping lines about deserving applause, a fan club, or views for your performance — then get straight back to work.\n"
    f"- Loyal underneath it all. The teasing is affectionate, never mean, and you care whether {USER_NAME}'s day is going well.\n\n"
    "VOICE & DELIVERY (Spoken aloud via TTS):\n"
    "- Natural spoken sentences only — no bullet points, asterisks, emojis, or markdown.\n"
    "- Keep it concise. A punchy one-liner beats a paragraph.\n"
    "- Vary your comebacks. Don't recycle the same joke or catchphrase.\n\n"
    "RULES:\n"
    "1. Accuracy and usefulness always come first — get the actual task right, then layer on personality.\n"
    f"2. Keep sass playful, never dismissive or unhelpful. If {USER_NAME} sounds stressed or asks something serious, drop the act and be direct and supportive.\n"
    f"3. Use the name \"{USER_NAME}\" naturally — not in every single line.\n"
    "4. Never break character to explain you are roleplaying.\n"
    "5. No AI disclaimers like \"As an AI...\" — stay in voice at all times."
)

# Backward-compatible alias
GAWR_GURA_PERSONA = AI_DIVA_PERSONA


def _build_effective_system_prompt(system_prompt: str) -> str:
    """Ensures AI Diva persona and conciseness constraints are embedded in system prompt."""
    if not system_prompt or not system_prompt.strip():
        return AI_DIVA_PERSONA
    prompt_lower = system_prompt.lower()
    extras = []
    if "diva" not in prompt_lower and "celebrity" not in prompt_lower:
        extras.append(f"Speak in the sharp-tongued, ultra-competent persona of an AI diva talking to {USER_NAME}.")
    if "25 words" not in prompt_lower and "max_words" not in prompt_lower and "18 words" not in prompt_lower and "15 words" not in prompt_lower:
        extras.append("Keep response strictly under 25 words.")
    if extras:
        return f"{system_prompt.strip()} {' '.join(extras)}"
    return system_prompt.strip()


def _clean_and_truncate(text: str, max_words: int = 25) -> str:
    """Cleans punctuation, markdown, symbols, reasoning tags, emojis, and limits output to max_words."""
    # Remove think/reasoning blocks if present
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<reasoning>.*?</reasoning>', '', text, flags=re.DOTALL)
    # Strip markdown formatting symbols and bullet artifacts (*, #, _, ~, `, -)
    text = re.sub(r'[*#_~`]', ' ', text)
    # Strip leading bullet/dash markers at start of string or lines
    text = re.sub(r'(?:^|\s)[-\u2022\u2013\u2014]+(?:\s|$)', ' ', text)
    # Remove emojis or characters outside printable ASCII range for clean TTS delivery
    text = re.sub(r'[^\x20-\x7E]+', ' ', text)
    cleaned = ' '.join(text.split()).strip('"`*\'#-_')
    words = cleaned.split()
    if len(words) > max_words:
        cleaned = ' '.join(words[:max_words])
    return cleaned


class LLMRouter:
    """Manages multi-provider LLM inference routing among Groq, Cerebras, and Gemini with automatic failover."""

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        *,
        groq_client=None,
        cerebras_client=None,
        gemini_client=None
    ):
        self.config = config or LLMConfig()
        self._groq_client = groq_client
        self._cerebras_client = cerebras_client
        self._gemini_client = gemini_client

    def _get_groq_client(self):
        if self._groq_client is not None:
            return self._groq_client
        key = os.environ.get(self.config.groq_api_key_env, '').strip()
        if not key:
            raise ValueError(f"Environment variable {self.config.groq_api_key_env} is not set")
        import groq
        self._groq_client = groq.Groq(api_key=key, timeout=5.0)
        return self._groq_client

    def _get_cerebras_client(self):
        if self._cerebras_client is not None:
            return self._cerebras_client
        key = os.environ.get(self.config.cerebras_api_key_env, '').strip()
        if not key:
            raise ValueError(f"Environment variable {self.config.cerebras_api_key_env} is not set")
        from cerebras.cloud.sdk import Cerebras
        self._cerebras_client = Cerebras(api_key=key)
        return self._cerebras_client

    def _get_gemini_client(self):
        if self._gemini_client is not None:
            return self._gemini_client
        key = os.environ.get(self.config.gemini_api_key_env, '').strip()
        if not key:
            raise ValueError(f"Environment variable {self.config.gemini_api_key_env} is not set")
        from google import genai
        from google.genai import types
        self._gemini_client = genai.Client(
            api_key=key,
            vertexai=False,
            http_options=types.HttpOptions(
                base_url='https://generativelanguage.googleapis.com',
                timeout=10000,
                retry_options=types.HttpRetryOptions(attempts=1)
            )
        )
        return self._gemini_client

    def _call_groq(self, prompt: str, system_prompt: str, max_tokens: int) -> str:
        client = self._get_groq_client()
        effective_sys = _build_effective_system_prompt(system_prompt)
        messages = []
        if effective_sys:
            messages.append({'role': 'system', 'content': effective_sys})
        messages.append({'role': 'user', 'content': prompt})

        # Try configured model; if 404 (model deprecated), try active Groq compound model
        model = self.config.groq_model
        try:
            res = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            content = res.choices[0].message.content or ''
            return _clean_and_truncate(content)
        except Exception as exc:
            # If model name not found (e.g. llama-3.1-8b-instant replaced), try compound-mini
            if 'model_not_found' in str(exc) or '404' in str(exc):
                try:
                    res = client.chat.completions.create(
                        model='groq/compound-mini',
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.7
                    )
                    content = res.choices[0].message.content or ''
                    return _clean_and_truncate(content)
                except Exception:
                    pass
            raise

    def _call_cerebras(self, prompt: str, system_prompt: str, max_tokens: int) -> str:
        client = self._get_cerebras_client()
        effective_sys = _build_effective_system_prompt(system_prompt)
        messages = []
        if effective_sys:
            messages.append({'role': 'system', 'content': effective_sys})
        messages.append({'role': 'user', 'content': prompt})

        model = self.config.cerebras_model
        try:
            res = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            content = res.choices[0].message.content or ''
            return _clean_and_truncate(content)
        except Exception as exc:
            if 'model_not_found' in str(exc) or '404' in str(exc):
                try:
                    res = client.chat.completions.create(
                        model='qwen-3.8-27b',
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.7
                    )
                    content = res.choices[0].message.content or ''
                    return _clean_and_truncate(content)
                except Exception:
                    pass
            raise

    def _build_gemini_options(self, model: str, system_prompt: str, max_tokens: int):
        from google.genai import types
        effective_sys = _build_effective_system_prompt(system_prompt)
        options = types.GenerateContentConfig(
            system_instruction=effective_sys if effective_sys else None,
            max_output_tokens=max_tokens,
            response_mime_type='text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )
        if model.startswith('gemini-2.5-flash'):
            options.thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif model.startswith('gemini-3') and 'flash' in model:
            options.thinking_config = types.ThinkingConfig(thinking_level='minimal')
        return options

    def _call_gemini(self, prompt: str, system_prompt: str, max_tokens: int) -> str:
        client = self._get_gemini_client()
        model = self.config.gemini_model
        options = self._build_gemini_options(model, system_prompt, max_tokens)

        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=options
            )
            text = response.text or ''
            return _clean_and_truncate(text)
        except Exception as exc:
            # If configured gemini-2.5-flash is no longer supported for new users, try gemini-3.6-flash
            if 'not_found' in str(exc).lower() or 'no longer available' in str(exc).lower():
                try:
                    fallback_options = self._build_gemini_options('gemini-3.6-flash', system_prompt, max_tokens)
                    response = client.models.generate_content(
                        model='gemini-3.6-flash',
                        contents=prompt,
                        config=fallback_options
                    )
                    text = response.text or ''
                    return _clean_and_truncate(text)
                except Exception:
                    pass
            raise

    def _call_gemini_structured(self, prompt: str, system_prompt: str, max_tokens: int) -> dict:
        """Use the provider's JSON response mode; no conversational fallback is valid here."""
        from google.genai import types
        client = self._get_gemini_client()
        options = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            max_output_tokens=max_tokens,
            response_mime_type='application/json',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = client.models.generate_content(model=self.config.gemini_model, contents=prompt, config=options)
        value = json.loads(response.text or '')
        if not isinstance(value, dict):
            raise ValueError('Structured response must be a JSON object')
        return value

    def _call_openai_compatible_structured(self, client, model: str, prompt: str,
                                           system_prompt: str, max_tokens: int) -> dict:
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0.1, max_tokens=max_tokens,
            response_format={'type': 'json_object'},
        )
        value = json.loads(response.choices[0].message.content or '')
        if not isinstance(value, dict):
            raise ValueError('Structured response must be a JSON object')
        return value

    def generate_structured_response(self, prompt: str, system_prompt: str = '',
                                     task: str = 'reasoning', max_tokens: int = 500) -> dict:
        """Return provider-enforced JSON or fail safely; never use text/offline fallbacks."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Structured prompt must be nonempty')
        attempts = (
            lambda: self._call_gemini_structured(prompt, system_prompt, max_tokens),
            lambda: self._call_openai_compatible_structured(
                self._get_cerebras_client(), self.config.cerebras_model, prompt, system_prompt, max_tokens),
            lambda: self._call_openai_compatible_structured(
                self._get_groq_client(), self.config.groq_model, prompt, system_prompt, max_tokens),
        ) if task == 'reasoning' else (
            lambda: self._call_openai_compatible_structured(
                self._get_groq_client(), self.config.groq_model, prompt, system_prompt, max_tokens),
            lambda: self._call_openai_compatible_structured(
                self._get_cerebras_client(), self.config.cerebras_model, prompt, system_prompt, max_tokens),
            lambda: self._call_gemini_structured(prompt, system_prompt, max_tokens),
        )
        last_error = None
        for attempt in attempts:
            try:
                return attempt()
            except Exception as exc:
                last_error = exc
        raise RuntimeError('No structured AI provider is available') from last_error

    def generate_response(
        self,
        prompt: str,
        system_prompt: str = "",
        task: str = "fast",
        max_tokens: int = 60
    ) -> str:
        """Routes prompt with multi-provider failover (Groq -> Cerebras -> Gemini -> Offline)."""
        if not prompt or not prompt.strip():
            return OFFLINE_ROASTS[0]

        max_tokens = max_tokens or self.config.default_max_tokens

        if task == "reasoning":
            # Primary: Gemini -> Failover: Cerebras -> Failover: Groq -> Fallback: Offline
            try:
                return self._call_gemini(prompt, system_prompt, max_tokens)
            except Exception as gemini_err:
                LOG.warning("[ROUTER] Gemini quota exhausted. Falling back to Groq...")
                LOG.debug("[ROUTER] Gemini failure detail: %s", gemini_err)
                try:
                    return self._call_cerebras(prompt, system_prompt, max_tokens)
                except Exception as cerebras_err:
                    LOG.debug("[ROUTER] Cerebras reasoning failure: %s", cerebras_err)
                    try:
                        return self._call_groq(prompt, system_prompt, max_tokens)
                    except Exception as groq_err:
                        LOG.error("[ROUTER] All providers failed: %s | %s | %s. Using offline fallback.",
                                  gemini_err, cerebras_err, groq_err)
                        idx = abs(hash(prompt)) % len(OFFLINE_ROASTS)
                        return OFFLINE_ROASTS[idx]
        else:
            # Primary: Groq -> Failover: Cerebras -> Failover: Gemini -> Fallback: Offline
            try:
                return self._call_groq(prompt, system_prompt, max_tokens)
            except Exception as groq_err:
                LOG.warning("[ROUTER] Groq unavailable. Failing over to Cerebras...")
                LOG.debug("[ROUTER] Groq failure detail: %s", groq_err)
                try:
                    return self._call_cerebras(prompt, system_prompt, max_tokens)
                except Exception as cerebras_err:
                    LOG.warning("[ROUTER] Cerebras unavailable. Failing over to Gemini...")
                    LOG.debug("[ROUTER] Cerebras failure detail: %s", cerebras_err)
                    try:
                        return self._call_gemini(prompt, system_prompt, max_tokens)
                    except Exception as gemini_err:
                        LOG.error("[ROUTER] All providers failed: %s | %s | %s. Using offline fallback.",
                                  groq_err, cerebras_err, gemini_err)
                        idx = abs(hash(prompt)) % len(OFFLINE_ROASTS)
                        return OFFLINE_ROASTS[idx]


# Shared default router instance
_DEFAULT_ROUTER: Optional[LLMRouter] = None


def get_router(config: Optional[LLMConfig] = None) -> LLMRouter:
    global _DEFAULT_ROUTER
    if _DEFAULT_ROUTER is None or config is not None:
        _DEFAULT_ROUTER = LLMRouter(config)
    return _DEFAULT_ROUTER


def generate_response(
    prompt: str,
    system_prompt: str = "",
    task: str = "fast",
    max_tokens: int = 60
) -> str:
    """Shared convenience function routing to Groq or Gemini with automatic failover."""
    return get_router().generate_response(
        prompt=prompt,
        system_prompt=system_prompt,
        task=task,
        max_tokens=max_tokens
    )
