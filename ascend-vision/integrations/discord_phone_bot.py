"""Optional, outbound Discord DM slash commands for the single phone-chat owner."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import os
import re
import time
from typing import Mapping
from urllib.parse import urlsplit

import discord
from discord import app_commands
import httpx


LOG = logging.getLogger(__name__)
_CODE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_STATUS_QUESTION = "What is the status of Ascend Hub AI agents?"
_ERROR = "Vision is unavailable right now. Please try again."
_LINK_FIRST = "This Discord account is not linked. Create a code in phone chat, then use /link."
_BUSY = "Vision is busy. Please try again in a moment."


@dataclass(frozen=True)
class DiscordBotSettings:
    bot_token: str
    application_id: int
    core_url: str
    bridge_token: str
    owner_id: str

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "DiscordBotSettings":
        env = os.environ if environ is None else environ
        names = (
            "ASCEND_DISCORD_BOT_TOKEN", "ASCEND_DISCORD_APPLICATION_ID",
            "ASCEND_PHONE_CORE_URL", "ASCEND_DISCORD_BRIDGE_TOKEN",
            "ASCEND_PHONE_OWNER_ID",
        )
        values = [env.get(name, "").strip() for name in names]
        if not all(values):
            raise ValueError("Discord bot requires its token, application ID, Core URL, bridge token, and owner ID")
        bot_token, raw_app_id, core_url, bridge_token, owner_id = values
        if not raw_app_id.isascii() or not raw_app_id.isdecimal() or not 0 < int(raw_app_id) <= 2**64 - 1:
            raise ValueError("Discord application ID must be a positive decimal snowflake")
        if bridge_token in {bot_token, env.get("ASCEND_PHONE_WORKER_TOKEN", "").strip()}:
            raise ValueError("Discord bridge token must differ from other transport tokens")
        _validate_core_url(core_url)
        return cls(bot_token, int(raw_app_id), core_url, bridge_token, owner_id)


def _validate_core_url(base_url: str) -> None:
    parts = urlsplit(base_url)
    if (parts.scheme not in {"https", "http"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in {"", "/"}
            or (parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Discord Core URL must be an HTTPS origin (loopback HTTP is allowed)")


class DiscordCoreClient:
    """Only the dedicated Core pairing bridge routes are reachable through this client."""

    def __init__(self, base_url: str, bridge_token: str, *, transport=None):
        _validate_core_url(base_url)
        if not isinstance(bridge_token, str) or not bridge_token.strip():
            raise ValueError("Discord bridge token is required")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=10.0, transport=transport,
            headers={"Authorization": f"Bearer {bridge_token.strip()}"},
        )

    async def consume_link(self, code: str, discord_user_id: str) -> bool:
        response = await self._client.post(
            "/api/phone-chat/worker/discord/consume-link",
            json={"code": code, "discordUserId": discord_user_id},
        )
        if response.status_code == 400:
            return False
        response.raise_for_status()
        payload = response.json()
        if payload != {"linked": True}:
            raise ValueError("Invalid Discord link response")
        return True

    async def verify_link(self, discord_user_id: str) -> str | None:
        response = await self._client.post(
            "/api/phone-chat/worker/discord/verify-link",
            json={"discordUserId": discord_user_id},
        )
        response.raise_for_status()
        payload = response.json()
        if payload == {"linked": False}:
            return None
        if (not isinstance(payload, dict) or set(payload) != {"linked", "ownerId"}
                or payload["linked"] is not True or not isinstance(payload["ownerId"], str)
                or not payload["ownerId"]):
            raise ValueError("Invalid Discord verification response")
        return payload["ownerId"]

    async def close(self) -> None:
        await self._client.aclose()


class _DiscordClient(discord.Client):
    def __init__(self, *, application_id: int, sync_commands: bool):
        super().__init__(intents=discord.Intents.none(), application_id=application_id)
        self.tree = app_commands.CommandTree(self)
        self._sync_commands = sync_commands

    async def setup_hook(self):
        if self._sync_commands:
            await self.tree.sync()


class DiscordPhoneBot:
    """DM-only command router; Core rechecks the active link on each chat command."""

    def __init__(self, core, handler, *, owner_id: str, token: str,
                 application_id: int, sync_commands: bool = False,
                 cooldown_seconds: float = 2.0, work_timeout_seconds: float = 600.0):
        if not owner_id or not token or type(application_id) is not int or application_id <= 0:
            raise ValueError("Discord bot identity is incomplete")
        if (type(cooldown_seconds) not in (int, float) or not math.isfinite(cooldown_seconds)
                or not 0 <= cooldown_seconds <= 60):
            raise ValueError("Discord cooldown must be between 0 and 60 seconds")
        if (type(work_timeout_seconds) not in (int, float) or not math.isfinite(work_timeout_seconds)
                or not 0 < work_timeout_seconds <= 600):
            raise ValueError("Discord work timeout must be between 0 and 600 seconds")
        self.core = core
        self._handler = handler
        self._owner_id = owner_id
        self._token = token
        self._cooldown_seconds = cooldown_seconds
        self._work_timeout_seconds = work_timeout_seconds
        self._closed = False
        self._active_owners: set[str] = set()
        self._last_chat_start: dict[str, float] = {}
        self.client = _DiscordClient(application_id=application_id, sync_commands=sync_commands)
        self.tree = self.client.tree
        self._register_commands()

    def _register_commands(self) -> None:
        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
        @app_commands.command(name="ask", description="Ask Vision in this private chat")
        async def ask(interaction: discord.Interaction, prompt: str):
            await self.handle_ask(interaction, prompt)

        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
        @app_commands.command(name="status", description="Read Ascend Hub AI status")
        async def status(interaction: discord.Interaction):
            await self.handle_status(interaction)

        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
        @app_commands.command(name="newchat", description="Clear this Discord chat context")
        async def newchat(interaction: discord.Interaction):
            await self.handle_newchat(interaction)

        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
        @app_commands.command(name="link", description="Link Discord with your phone-chat code")
        async def link(interaction: discord.Interaction, code: str):
            await self.handle_link(interaction, code)

        for command in (ask, status, newchat, link):
            self.tree.add_command(command)

    async def handle_ask(self, interaction, prompt: str) -> None:
        await self._run(interaction, "ask", prompt)

    async def handle_status(self, interaction) -> None:
        await self._run(interaction, "status")

    async def handle_newchat(self, interaction) -> None:
        await self._run(interaction, "newchat")

    async def handle_link(self, interaction, code: str) -> None:
        await self._run(interaction, "link", code)

    async def _run(self, interaction, command: str, value: str | None = None) -> None:
        if self._closed:
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as exc:
            LOG.warning("Discord %s defer failed (%s)", command, type(exc).__name__)
            return
        if self._closed:
            return
        if interaction.guild_id is not None:
            await self._edit(interaction, "Use this command in a DM with Vision.", command)
            return
        try:
            user_id = str(interaction.user.id)
            if command == "link":
                if not isinstance(value, str) or _CODE.fullmatch(value) is None:
                    result = "That pairing code is invalid or expired. Create a new one in phone chat."
                elif await self.core.consume_link(value, user_id):
                    result = "Discord is linked. You can use /ask and /status here."
                else:
                    result = "That pairing code is invalid or expired. Create a new one in phone chat."
            else:
                owner = await self.core.verify_link(user_id)
                if owner != self._owner_id:
                    result = _LINK_FIRST
                elif command == "newchat":
                    result = await self._run_handler(owner, command, user_id)
                else:
                    prompt = _STATUS_QUESTION if command == "status" else value
                    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4_000:
                        result = "Enter a message between 1 and 4000 characters."
                    else:
                        result = await self._run_handler(owner, command, user_id, prompt)
            if not isinstance(result, str) or not result.strip():
                raise ValueError("Empty Discord command response")
            await self._edit(interaction, result[:1900], command)
        except Exception as exc:
            LOG.warning("Discord %s failed (%s)", command, type(exc).__name__)
            await self._edit(interaction, _ERROR, command)

    async def _run_handler(self, owner: str, command: str, user_id: str,
                           prompt: str | None = None) -> str:
        if self._closed:
            return _ERROR
        # One owner, one running turn, zero queued turns. The reservation is made
        # without yielding, so simultaneous interactions cannot both enter.
        now = time.monotonic()
        if (owner in self._active_owners or
                (command != "newchat" and
                 now - self._last_chat_start.get(owner, float("-inf")) < self._cooldown_seconds)):
            return _BUSY
        self._active_owners.add(owner)
        if command != "newchat":
            self._last_chat_start[owner] = now
        task = None
        try:
            if command == "newchat":
                task = asyncio.create_task(asyncio.to_thread(
                    self._handler.clear_session, owner, "discord_dm", user_id,
                ))
            else:
                task = asyncio.create_task(asyncio.to_thread(
                    self._handler.handle, owner, "discord_dm", user_id, prompt,
                ))
            task.add_done_callback(lambda completed: self._finish_work(owner, completed))
            result = await asyncio.wait_for(asyncio.shield(task), self._work_timeout_seconds)
            return "This Discord chat context is cleared." if command == "newchat" else result.text
        finally:
            # A timed-out await leaves the shielded thread running and the slot
            # reserved. Its done callback releases the slot only when work ends.
            if task is None or task.done():
                self._active_owners.discard(owner)

    def _finish_work(self, owner: str, task: asyncio.Task) -> None:
        self._active_owners.discard(owner)
        if not task.cancelled():
            task.exception()  # Consume late failures without logging private details.

    async def _edit(self, interaction, message: str, command: str) -> None:
        try:
            if not self._closed and not interaction.is_expired():
                await interaction.edit_original_response(
                    content=message, allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception as exc:
            LOG.warning("Discord %s reply failed (%s)", command, type(exc).__name__)

    async def start(self) -> None:
        await self.client.start(self._token)

    async def close(self) -> None:
        self._closed = True
        await self.client.close()
        await self.core.close()
