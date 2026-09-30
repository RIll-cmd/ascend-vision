import os

import pytest

from assistant.runtime_environment import load_runtime_environment


def test_explicit_env_file_is_loaded_without_overriding_process_values(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("", encoding="utf-8")
    secrets = tmp_path / "owner-secrets.env"
    secrets.write_text("GROQ_API_KEY=file-fixture\nSECOND_PROVIDER_KEY=second-fixture\n", encoding="utf-8")
    monkeypatch.setenv("GROQ_API_KEY", "inherited-fixture")
    monkeypatch.delenv("SECOND_PROVIDER_KEY", raising=False)

    selected = load_runtime_environment(config, secrets)

    assert selected == secrets.resolve()
    assert os.environ["GROQ_API_KEY"] == "inherited-fixture"
    assert os.environ["SECOND_PROVIDER_KEY"] == "second-fixture"


def test_missing_explicit_env_file_fails_before_startup(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="explicit env file"):
        load_runtime_environment(config, tmp_path / "missing.env")


def test_default_env_file_is_selected_from_config_directory(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("", encoding="utf-8")
    secrets = tmp_path / ".env"
    secrets.write_text("GEMINI_API_KEY=local-fixture\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    selected = load_runtime_environment(config)

    assert selected == secrets.resolve()
    assert os.environ["GEMINI_API_KEY"] == "local-fixture"
