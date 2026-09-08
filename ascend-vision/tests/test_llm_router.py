"""Comprehensive unit tests for the multi-provider Groq/Cerebras/Gemini LLMRouter."""
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

from config import LLMConfig
import llm_router
from llm_router import (
    LLMRouter,
    generate_response,
    _clean_and_truncate,
    _build_effective_system_prompt,
    OFFLINE_ROASTS,
)


@pytest.fixture
def mock_groq():
    client = Mock()
    res = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Groq says: put the phone down!"))]
    )
    client.chat.completions.create.return_value = res
    return client


@pytest.fixture
def mock_cerebras():
    client = Mock()
    res = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Cerebras says: sit up straight!"))]
    )
    client.chat.completions.create.return_value = res
    return client


@pytest.fixture
def mock_gemini():
    client = Mock()
    res = SimpleNamespace(
        text="Gemini says: sit up straight like a proper shark!"
    )
    client.models.generate_content.return_value = res
    return client


def test_clean_and_truncate_removes_think_and_limits_words():
    raw = "<think>Internal reasoning here</think> Put your phone away right now! " + ("word " * 40)
    cleaned = _clean_and_truncate(raw, max_words=25)
    assert "<think>" not in cleaned
    assert len(cleaned.split()) <= 25
    assert cleaned.startswith("Put your phone away")


def test_clean_and_truncate_strips_symbols_and_emojis():
    raw = "## *Hey there!* - Put the #phone down! ~immediately~ `dummy` 📱✨"
    cleaned = _clean_and_truncate(raw, max_words=25)
    assert "*" not in cleaned
    assert "#" not in cleaned
    assert "-" not in cleaned
    assert "~" not in cleaned
    assert "`" not in cleaned
    assert "📱" not in cleaned
    assert "✨" not in cleaned
    assert "Hey there! Put the phone down! immediately dummy" in cleaned


def test_build_effective_system_prompt_diva_cb():
    prompt = _build_effective_system_prompt("You are a focus coach.")
    assert "diva" in prompt
    assert "CB" in prompt
    assert "25 words" in prompt

    empty_prompt = _build_effective_system_prompt("")
    assert "diva" in empty_prompt
    assert "CB" in empty_prompt
    assert "natural spoken sentences only" in empty_prompt.lower()


def test_fast_path_routes_to_groq_first(mock_groq, mock_cerebras, mock_gemini):
    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    res = router.generate_response(prompt="Stop distracting me", system_prompt="You are Gura", task="fast")
    assert "Groq says" in res
    assert mock_groq.chat.completions.create.call_count == 1
    assert mock_cerebras.chat.completions.create.call_count == 0
    assert mock_gemini.models.generate_content.call_count == 0


def test_reasoning_path_routes_to_gemini_first(mock_groq, mock_cerebras, mock_gemini):
    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    res = router.generate_response(prompt="Analyze session", system_prompt="You are Gura", task="reasoning")
    assert "Gemini says" in res
    assert mock_gemini.models.generate_content.call_count == 1
    assert mock_groq.chat.completions.create.call_count == 0
    assert mock_cerebras.chat.completions.create.call_count == 0


def test_structured_response_uses_json_mode_and_never_returns_offline_text(mock_groq, mock_cerebras, mock_gemini):
    mock_gemini.models.generate_content.return_value = SimpleNamespace(text='{"kind":"proposal","draft":{}}')
    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)

    result = router.generate_structured_response(prompt='{}', system_prompt='Return JSON', task='reasoning', max_tokens=100)

    assert result == {"kind": "proposal", "draft": {}}
    options = mock_gemini.models.generate_content.call_args.kwargs['config']
    assert options.response_mime_type == 'application/json'


def test_groq_failure_fails_over_to_cerebras(mock_groq, mock_cerebras, mock_gemini, caplog):
    import groq
    mock_groq.chat.completions.create.side_effect = groq.RateLimitError(
        message="Rate limit exceeded",
        response=Mock(status_code=429, headers={}),
        body=None
    )

    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    with caplog.at_level(logging.WARNING):
        res = router.generate_response(prompt="Get to work", system_prompt="You are Gura", task="fast")

    assert "[ROUTER] Groq unavailable. Failing over to Cerebras..." in caplog.text
    assert "Cerebras says" in res
    assert mock_groq.chat.completions.create.call_count >= 1
    assert mock_cerebras.chat.completions.create.call_count == 1
    assert mock_gemini.models.generate_content.call_count == 0


def test_cerebras_failure_fails_over_to_gemini(mock_groq, mock_cerebras, mock_gemini, caplog):
    mock_groq.chat.completions.create.side_effect = Exception("Groq connection timeout")
    mock_cerebras.chat.completions.create.side_effect = Exception("Cerebras 402 payment required")

    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    with caplog.at_level(logging.WARNING):
        res = router.generate_response(prompt="Get to work", system_prompt="You are Gura", task="fast")

    assert "[ROUTER] Groq unavailable. Failing over to Cerebras..." in caplog.text
    assert "[ROUTER] Cerebras unavailable. Failing over to Gemini..." in caplog.text
    assert "Gemini says" in res
    assert mock_gemini.models.generate_content.call_count == 1


def test_gemini_exhausted_fails_over_to_cerebras_and_groq(mock_groq, mock_cerebras, mock_gemini, caplog):
    mock_gemini.models.generate_content.side_effect = RuntimeError("429 Resource Exhausted")

    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    with caplog.at_level(logging.WARNING):
        res = router.generate_response(prompt="Summarize posture", system_prompt="You are Gura", task="reasoning")

    assert "[ROUTER] Gemini quota exhausted. Falling back to Groq..." in caplog.text
    assert "Cerebras says" in res
    assert mock_gemini.models.generate_content.call_count >= 1
    assert mock_cerebras.chat.completions.create.call_count == 1


def test_all_fail_returns_offline_fallback(mock_groq, mock_cerebras, mock_gemini, caplog):
    mock_groq.chat.completions.create.side_effect = Exception("Groq down")
    mock_cerebras.chat.completions.create.side_effect = Exception("Cerebras down")
    mock_gemini.models.generate_content.side_effect = Exception("Gemini down")

    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    with caplog.at_level(logging.WARNING):
        res = router.generate_response(prompt="I am slouching", system_prompt="You are Gura", task="fast")

    assert res in OFFLINE_ROASTS
    assert "[ROUTER] Groq unavailable. Failing over to Cerebras..." in caplog.text
    assert "[ROUTER] Cerebras unavailable. Failing over to Gemini..." in caplog.text


def test_empty_prompt_returns_offline_roast_without_api_call(mock_groq, mock_cerebras, mock_gemini):
    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    res = router.generate_response(prompt="", system_prompt="You are Gura")
    assert res in OFFLINE_ROASTS
    assert mock_groq.chat.completions.create.call_count == 0
    assert mock_cerebras.chat.completions.create.call_count == 0
    assert mock_gemini.models.generate_content.call_count == 0


def test_shared_generate_response_function():
    with patch("llm_router.get_router") as mock_get_router:
        mock_instance = Mock()
        mock_instance.generate_response.return_value = "Keep focusing!"
        mock_get_router.return_value = mock_instance

        res = generate_response("Hello", "Persona", task="fast", max_tokens=40)
        assert res == "Keep focusing!"
        mock_instance.generate_response.assert_called_once_with(
            prompt="Hello",
            system_prompt="Persona",
            task="fast",
            max_tokens=40
        )


def test_llm_config_defaults_and_validation():
    cfg = LLMConfig()
    assert cfg.groq_model == "llama-3.1-8b-instant"
    assert cfg.cerebras_model == "llama3.1-8b"
    assert cfg.gemini_model == "gemini-2.5-flash"
    assert cfg.groq_api_key_env == "GROQ_API_KEY"
    assert cfg.cerebras_api_key_env == "CEREBRAS_API_KEY"
    assert cfg.gemini_api_key_env == "GEMINI_API_KEY"
    assert cfg.default_max_tokens == 60

    with pytest.raises(ValueError):
        LLMConfig(groq_model="")
    with pytest.raises(ValueError):
        LLMConfig(cerebras_model="")
    with pytest.raises(ValueError):
        LLMConfig(gemini_model="")
    with pytest.raises(ValueError):
        LLMConfig(cerebras_api_key_env="INVALID-KEY")
    with pytest.raises(ValueError):
        LLMConfig(default_max_tokens=5)


def test_llm_roaster_integration(mock_groq, mock_cerebras, mock_gemini):
    from feedback import LLMRoaster, RoastContext, ConversationContext
    router = LLMRouter(groq_client=mock_groq, cerebras_client=mock_cerebras, gemini_client=mock_gemini)
    roaster = LLMRoaster(router=router)

    # Test roast generation
    ctx = RoastContext(5, 1, 15.0, "10:30", event_type="phone_held")
    roast = roaster.generate(ctx)
    assert "Groq says" in roast

    # Test chat generation
    chat_ctx = ConversationContext("Are you watching?", phone_pickups=1)
    reply = roaster.generate_chat("Are you watching?", chat_ctx)
    assert "Groq says" in reply

    roaster.close()
