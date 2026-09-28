"""Dual LLM Router supporting Groq and Gemini with bidirectional automatic failover."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

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


@dataclass(frozen=True)
class BrowserStructuredResponse:
    data: dict
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class ProviderKeyPool:
    """Thread-safe pool of API keys for a provider with automatic rotation upon limits/errors."""

    def __init__(
        self,
        provider: str,
        keys: list[str],
        client_factory,
        explicit_client=None,
        cooldown_seconds: float = 60.0,
    ):
        self.provider = provider
        self.keys = [k.strip() for k in keys if k and k.strip()]
        self.client_factory = client_factory
        self.explicit_client = explicit_client
        self.cooldown_seconds = cooldown_seconds
        self._clients: dict[str, Any] = {}
        self._cooldown_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._current_index = 0

    @property
    def has_keys(self) -> bool:
        return self.explicit_client is not None or len(self.keys) > 0

    def get_current_client(self):
        """Returns the currently active client or None if none configured."""
        if self.explicit_client is not None:
            return self.explicit_client
        if not self.keys:
            return None
        with self._lock:
            key = self.keys[self._current_index % len(self.keys)]
            if key not in self._clients:
                self._clients[key] = self.client_factory(key)
            return self._clients[key]

    def execute_with_failover(self, call_fn):
        """Executes call_fn(client). If rotatable limit/error occurs and backup keys exist, rotates and retries."""
        if self.explicit_client is not None:
            return call_fn(self.explicit_client)

        if not self.keys:
            raise ValueError(f"No API keys configured for provider '{self.provider}'.")

        now = time.monotonic()
        total_keys = len(self.keys)
        if total_keys == 1:
            key = self.keys[0]
            with self._lock:
                if key not in self._clients:
                    self._clients[key] = self.client_factory(key)
                client = self._clients[key]
            return call_fn(client)

        with self._lock:
            start_index = self._current_index

        last_error = None
        for attempt in range(total_keys):
            idx = (start_index + attempt) % total_keys
            key = self.keys[idx]

            with self._lock:
                cooldown = self._cooldown_until.get(key, 0.0)
                # If on cooldown, skip it unless this is our last option
                if now < cooldown and attempt < total_keys - 1:
                    continue
                if key not in self._clients:
                    try:
                        self._clients[key] = self.client_factory(key)
                    except Exception as exc:
                        last_error = exc
                        continue
                client = self._clients[key]

            try:
                res = call_fn(client)
                with self._lock:
                    self._current_index = idx
                return res
            except Exception as exc:
                last_error = exc
                if self._is_rotatable_error(exc):
                    with self._lock:
                        self._cooldown_until[key] = time.monotonic() + self.cooldown_seconds
                        self._current_index = (idx + 1) % total_keys
                    LOG.warning(
                        "[ROUTER] %s key %d/%d encountered limit/error (%s). Switching to backup key...",
                        self.provider.upper(), idx + 1, total_keys, exc
                    )
                    time.sleep(0.5)
                else:
                    raise

        raise last_error

    @staticmethod
    def _is_rotatable_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        status_code = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
        if status_code in (429, 503, 403, 504):
            return True
        rotatable_indicators = (
            '429', '503', '504', 'rate limit', 'rate_limit', 'quota', 'resource_exhausted',
            'resourceexhausted', 'too many requests', 'high demand', 'temporarily unavailable',
            'deadline expired', 'deadline_exceeded', 'project has been denied access', 'permission_denied'
        )
        return any(ind in msg for ind in rotatable_indicators)


def _build_gemini_thinking_config(model: str):
    from google.genai import types
    if model.startswith('gemini-2.5-flash'):
        return types.ThinkingConfig(thinking_budget=0)
    elif (model.startswith('gemini-3') or 'thinking' in model) and 'flash' in model:
        return types.ThinkingConfig(thinking_level='minimal')
    return None


class LLMRouter:
    """Manages multi-provider LLM inference routing among Groq, Cerebras, and Gemini with automatic failover."""

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        *,
        groq_client=None,
        cerebras_client=None,
        gemini_client=None,
        groq_clients: Optional[list] = None,
        cerebras_clients: Optional[list] = None,
        gemini_clients: Optional[list] = None,
    ):
        self.config = config or LLMConfig()
        self._groq_client = groq_client
        self._cerebras_client = cerebras_client
        self._gemini_client = gemini_client

        # Initialize pools for each provider
        groq_keys = self._parse_keys(self.config.groq_api_key_env)
        cerebras_keys = self._parse_keys(self.config.cerebras_api_key_env)
        gemini_keys = self._parse_keys(self.config.gemini_api_key_env)

        self._groq_pool = ProviderKeyPool(
            'groq', groq_keys, self._create_groq_client,
            explicit_client=groq_client if groq_clients is None else None
        )
        self._cerebras_pool = ProviderKeyPool(
            'cerebras', cerebras_keys, self._create_cerebras_client,
            explicit_client=cerebras_client if cerebras_clients is None else None
        )
        self._gemini_pool = ProviderKeyPool(
            'gemini', gemini_keys, self._create_gemini_client,
            explicit_client=gemini_client if gemini_clients is None else None
        )

        if groq_clients:
            self._groq_pool.keys = [f"mock_groq_{i}" for i in range(len(groq_clients))]
            self._groq_pool._clients = {k: c for k, c in zip(self._groq_pool.keys, groq_clients)}
        if cerebras_clients:
            self._cerebras_pool.keys = [f"mock_cerebras_{i}" for i in range(len(cerebras_clients))]
            self._cerebras_pool._clients = {k: c for k, c in zip(self._cerebras_pool.keys, cerebras_clients)}
        if gemini_clients:
            self._gemini_pool.keys = [f"mock_gemini_{i}" for i in range(len(gemini_clients))]
            self._gemini_pool._clients = {k: c for k, c in zip(self._gemini_pool.keys, gemini_clients)}

    @staticmethod
    def _parse_keys(env_var_name: str) -> list[str]:
        raw = os.environ.get(env_var_name, '').strip()
        if not raw and os.name == 'nt':
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as key:
                    raw = (winreg.QueryValueEx(key, env_var_name)[0] or '').strip()
            except Exception:
                pass
        if not raw:
            return []
        normalized = raw.replace('\n', ',').replace(';', ',')
        return [k.strip() for k in normalized.split(',') if k.strip()]

    def _create_groq_client(self, key: str):
        import groq
        return groq.Groq(api_key=key, timeout=5.0)

    def _create_cerebras_client(self, key: str):
        from cerebras.cloud.sdk import Cerebras
        return Cerebras(api_key=key)

    def _create_gemini_client(self, key: str):
        from google import genai
        from google.genai import types
        return genai.Client(
            api_key=key,
            vertexai=False,
            http_options=types.HttpOptions(
                base_url='https://generativelanguage.googleapis.com',
                timeout=15000,
                retry_options=types.HttpRetryOptions(attempts=1)
            )
        )

    def _get_groq_client(self):
        if self._groq_client is not None:
            return self._groq_client
        client = self._groq_pool.get_current_client()
        if client is None:
            raise ValueError(f"Environment variable {self.config.groq_api_key_env} is not set")
        return client

    def _get_cerebras_client(self):
        if self._cerebras_client is not None:
            return self._cerebras_client
        client = self._cerebras_pool.get_current_client()
        if client is None:
            raise ValueError(f"Environment variable {self.config.cerebras_api_key_env} is not set")
        return client

    def _get_gemini_client(self):
        if self._gemini_client is not None:
            return self._gemini_client
        client = self._gemini_pool.get_current_client()
        if client is None:
            raise ValueError(f"Environment variable {self.config.gemini_api_key_env} is not set")
        return client

    def _call_groq(self, prompt: str, system_prompt: str, max_tokens: int) -> str:
        effective_sys = _build_effective_system_prompt(system_prompt)
        messages = []
        if effective_sys:
            messages.append({'role': 'system', 'content': effective_sys})
        messages.append({'role': 'user', 'content': prompt})

        model = self.config.groq_model

        def _do_call(client):
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

        return self._groq_pool.execute_with_failover(_do_call)

    def _call_cerebras(self, prompt: str, system_prompt: str, max_tokens: int) -> str:
        effective_sys = _build_effective_system_prompt(system_prompt)
        messages = []
        if effective_sys:
            messages.append({'role': 'system', 'content': effective_sys})
        messages.append({'role': 'user', 'content': prompt})

        model = self.config.cerebras_model

        def _do_call(client):
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

        return self._cerebras_pool.execute_with_failover(_do_call)

    def _build_gemini_options(self, model: str, system_prompt: str, max_tokens: int):
        from google.genai import types
        effective_sys = _build_effective_system_prompt(system_prompt)
        return types.GenerateContentConfig(
            system_instruction=effective_sys if effective_sys else None,
            max_output_tokens=max_tokens,
            response_mime_type='text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=_build_gemini_thinking_config(model),
        )

    def _call_gemini(self, prompt: str, system_prompt: str, max_tokens: int) -> str:
        model = self.config.gemini_model
        options = self._build_gemini_options(model, system_prompt, max_tokens)

        def _do_call(client):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=options
                )
                text = response.text or ''
                return _clean_and_truncate(text)
            except Exception as exc:
                # If configured gemini model is no longer supported for new users, try alternatives
                if 'not_found' in str(exc).lower() or 'no longer available' in str(exc).lower():
                    for fallback_model in ('gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-flash-latest'):
                        if fallback_model == model:
                            continue
                        try:
                            fallback_options = self._build_gemini_options(fallback_model, system_prompt, max_tokens)
                            response = client.models.generate_content(
                                model=fallback_model,
                                contents=prompt,
                                config=fallback_options
                            )
                            text = response.text or ''
                            return _clean_and_truncate(text)
                        except Exception:
                            pass
                raise

        return self._gemini_pool.execute_with_failover(_do_call)

    def _call_gemini_structured(self, prompt: str, system_prompt: str, max_tokens: int) -> dict:
        """Use the provider's JSON response mode; no conversational fallback is valid here."""
        from google.genai import types
        options = types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            max_output_tokens=max_tokens,
            response_mime_type='application/json',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=_build_gemini_thinking_config(self.config.gemini_model),
        )

        def _do_call(client):
            try:
                response = client.models.generate_content(
                    model=self.config.gemini_model, contents=prompt, config=options
                )
            except Exception as exc:
                if 'not_found' in str(exc).lower() or 'no longer available' in str(exc).lower():
                    for fallback_model in ('gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-flash-latest'):
                        if fallback_model == self.config.gemini_model:
                            continue
                        try:
                            fallback_options = types.GenerateContentConfig(
                                system_instruction=system_prompt or None,
                                max_output_tokens=max_tokens,
                                response_mime_type='application/json',
                                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                thinking_config=_build_gemini_thinking_config(fallback_model),
                            )
                            response = client.models.generate_content(
                                model=fallback_model, contents=prompt, config=fallback_options
                            )
                            break
                        except Exception:
                            pass
                    else:
                        raise
                else:
                    raise
            value = json.loads(response.text or '')
            if not isinstance(value, dict):
                raise ValueError('Structured response must be a JSON object')
            return value

        return self._gemini_pool.execute_with_failover(_do_call)

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

    def _call_groq_structured(self, prompt: str, system_prompt: str, max_tokens: int) -> dict:
        return self._groq_pool.execute_with_failover(
            lambda client: self._call_openai_compatible_structured(
                client, self.config.groq_model, prompt, system_prompt, max_tokens
            )
        )

    def _call_cerebras_structured(self, prompt: str, system_prompt: str, max_tokens: int) -> dict:
        return self._cerebras_pool.execute_with_failover(
            lambda client: self._call_openai_compatible_structured(
                client, self.config.cerebras_model, prompt, system_prompt, max_tokens
            )
        )

    def generate_structured_response(self, prompt: str, system_prompt: str = '',
                                     task: str = 'reasoning', max_tokens: int = 500) -> dict:
        """Return provider-enforced JSON or fail safely; never use text/offline fallbacks."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Structured prompt must be nonempty')
        attempts = (
            lambda: self._call_gemini_structured(prompt, system_prompt, max_tokens),
            lambda: self._call_cerebras_structured(prompt, system_prompt, max_tokens),
            lambda: self._call_groq_structured(prompt, system_prompt, max_tokens),
        ) if task == 'reasoning' else (
            lambda: self._call_groq_structured(prompt, system_prompt, max_tokens),
            lambda: self._call_cerebras_structured(prompt, system_prompt, max_tokens),
            lambda: self._call_gemini_structured(prompt, system_prompt, max_tokens),
        )
        last_error = None
        for attempt in attempts:
            try:
                return attempt()
            except Exception as exc:
                last_error = exc
        raise RuntimeError('No structured AI provider is available') from last_error

    def generate_browser_structured_response(self, provider: str, prompt: str,
                                             system_prompt: str = '', max_tokens: int = 500):
        """Use exactly one owner-selected provider for browser page data; never fail over."""
        if provider not in {'gemini', 'cerebras', 'groq'}:
            raise ValueError('Unknown browser decision provider')
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Browser decision prompt must be nonempty')
        if type(max_tokens) is not int or not 1 <= max_tokens <= 2_000:
            raise ValueError('Browser decision max_tokens must be between 1 and 2000')
        model = {
            'gemini': self.config.gemini_model,
            'cerebras': self.config.cerebras_model,
            'groq': self.config.groq_model,
        }[provider]

        def _do_call(client):
            started = time.monotonic()
            if provider == 'gemini':
                from google.genai import types
                thinking_cfg = _build_gemini_thinking_config(model)
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt or None,
                            max_output_tokens=max_tokens,
                            response_mime_type='application/json',
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                            thinking_config=thinking_cfg,
                        ),
                    )
                except Exception as exc:
                    if 'not_found' in str(exc).lower() or 'no longer available' in str(exc).lower():
                        for fallback_model in ('gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-flash-latest'):
                            if fallback_model == model:
                                continue
                            try:
                                fallback_thinking = _build_gemini_thinking_config(fallback_model)
                                response = client.models.generate_content(
                                    model=fallback_model,
                                    contents=prompt,
                                    config=types.GenerateContentConfig(
                                        system_instruction=system_prompt or None,
                                        max_output_tokens=max_tokens,
                                        response_mime_type='application/json',
                                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                        thinking_config=fallback_thinking,
                                    ),
                                )
                                break
                            except Exception:
                                pass
                        else:
                            raise
                    else:
                        raise
                raw = response.text or ''
                usage = getattr(response, 'usage_metadata', None)
                input_tokens = getattr(usage, 'prompt_token_count', None)
                output_tokens = getattr(usage, 'candidates_token_count', None)
            else:
                messages = []
                if system_prompt:
                    messages.append({'role': 'system', 'content': system_prompt})
                messages.append({'role': 'user', 'content': prompt})
                response = client.chat.completions.create(
                    model=model, messages=messages, temperature=0.1, max_tokens=max_tokens,
                    response_format={'type': 'json_object'},
                )
                raw = response.choices[0].message.content or ''
                usage = getattr(response, 'usage', None)
                input_tokens = getattr(usage, 'prompt_tokens', None)
                output_tokens = getattr(usage, 'completion_tokens', None)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Structured response must be a JSON object')
            return BrowserStructuredResponse(
                data=data, provider=provider, model=model,
                input_tokens=input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
                output_tokens=output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
                latency_ms=max(0, round((time.monotonic() - started) * 1_000)),
            )

        pool = {
            'gemini': self._gemini_pool,
            'cerebras': self._cerebras_pool,
            'groq': self._groq_pool,
        }[provider]

        try:
            return pool.execute_with_failover(_do_call)
        except Exception as exc:
            raise RuntimeError(f'No response from the selected browser provider ({provider}).') from exc


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
