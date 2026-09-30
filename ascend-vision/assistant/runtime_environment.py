"""Load an explicitly selected owner environment without moving secrets."""
from __future__ import annotations

from pathlib import Path


def load_runtime_environment(config_path: Path, env_file: Path | None = None) -> Path:
    """Load a config-adjacent or explicitly selected .env, keeping OS values first."""
    if not isinstance(config_path, Path):
        raise TypeError("config_path must be a Path")
    explicit = env_file is not None
    selected = (env_file.expanduser().resolve() if explicit
                else config_path.expanduser().resolve().parent / ".env")
    if explicit and not selected.is_file():
        raise FileNotFoundError("The explicit env file is missing.")
    if selected.is_file():
        from dotenv import load_dotenv

        load_dotenv(selected, override=False)
    return selected
