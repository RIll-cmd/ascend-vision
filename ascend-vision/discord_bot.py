"""Standalone Discord DM bot. Run explicitly from the Vision project directory."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import signal
import sqlite3

from assistant.memory import MemoryStore, UnavailableMemoryStore
from assistant.phone_handler import PhoneMessageHandler
from assistant.service import AssistantService
from assistant.tool_runtime import ToolRuntime, ToolSpec
from config import load_config
from integrations.discord_phone_bot import DiscordBotSettings, DiscordCoreClient, DiscordPhoneBot
from integrations.status_shelf import ShelfSnapshot, StatusShelfReader


LOG = logging.getLogger(__name__)


def configure_private_logging() -> None:
    """Provider failures can contain request bodies, so suppress their detail logger."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("llm_router").setLevel(logging.CRITICAL)


def build_discord_bot(settings: DiscordBotSettings, config, *, sync_commands: bool = False):
    """Build the isolated bot without starting desktop, camera, or HTTP listeners."""
    try:
        memory_store = MemoryStore(Path(config.storage.database).parent / "assistant_memory.db")
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        LOG.warning("Discord memory unavailable (%s)", type(exc).__name__)
        memory_store = UnavailableMemoryStore()

    status_runtime = None
    status_credential = os.getenv("ASCEND_STATUS_READ_CREDENTIAL", "").strip()
    if status_credential:
        reader = StatusShelfReader(settings.core_url, status_credential, timeout_seconds=3.0)
        status_runtime = ToolRuntime()
        status_runtime.register(
            ToolSpec("hub_status", 1, "read-only", frozenset(), ShelfSnapshot),
            reader.read,
        )

    assistant = AssistantService(config.feedback, config.llm, memory_store=memory_store,
                                 tool_runtime=status_runtime)
    handler = PhoneMessageHandler(assistant, owner_id=settings.owner_id,
                                  allowed_channels=frozenset({"discord_dm"}))
    core = DiscordCoreClient(settings.core_url, settings.bridge_token)
    return DiscordPhoneBot(core, handler, owner_id=settings.owner_id,
                           token=settings.bot_token, application_id=settings.application_id,
                           sync_commands=sync_commands)


async def _serve(bot: DiscordPhoneBot) -> None:
    """Stop on SIGTERM where supported; asyncio.run handles Ctrl+C cancellation."""
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    prior = None
    try:
        prior = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: loop.call_soon_threadsafe(task.cancel))
    except (ValueError, OSError):
        prior = None
    try:
        await bot.start()
    finally:
        if prior is not None:
            signal.signal(signal.SIGTERM, prior)
        await bot.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Optional Ascend Vision Discord DM bot")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--sync-commands", action="store_true",
                        help="Explicitly publish the four global slash commands at startup")
    args = parser.parse_args(argv)
    configure_private_logging()
    try:
        settings = DiscordBotSettings.from_environ()
    except ValueError as exc:
        LOG.error("Discord bot configuration failed: %s", exc)
        return 2
    try:
        config = load_config(args.config)
        bot = build_discord_bot(settings, config, sync_commands=args.sync_commands)
    except (ValueError, OSError) as exc:
        LOG.error("Discord bot configuration failed (%s)", type(exc).__name__)
        return 2
    try:
        asyncio.run(_serve(bot))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        LOG.error("Discord bot stopped (%s)", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
