"""Discord browser consent and task receipts; execution belongs to Core's queue."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import re
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import discord
import httpx

_NAMESPACE = UUID("9672db28-e5b6-42bf-adc4-f0ea0ef4ce1d")
_PROVIDERS = {"gemini", "cerebras", "groq"}
_STATES = {"queued", "claimed", "running", "paused", "waiting_for_user", "stopping",
           "completed", "partial", "failed", "unknown", "cancelled", "expired"}
_OVERRIDE = re.compile(
    r'\b(?:owner(?:_?id)?|discord_?user_?id|laptop(?:_?id)?|provider(?:_?consent)?|'
    r'profile(?:_?id)?|origin|path|browser_?session_?id|scope(?:_?id|_?version)?|'
    r'expected_?link_?generation)[\"\x27]?\s*[:=]', re.IGNORECASE,
)


def validate_pwa_origin(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.path not in {"", "/"} or parts.query or parts.fragment
            or any(char.isspace() for char in value)):
        raise ValueError("Discord browser handoff requires a configured HTTPS PWA origin")
    parts.port  # Reject malformed ports before accepting configuration.
    return value.rstrip("/")


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid browser UUID")
    return value


def _expiry(value):
    if not isinstance(value, str):
        raise ValueError("Invalid browser expiry")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Browser expiry requires a timezone")


def validate_bridge_response(command, payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid browser bridge response")
    if command == "start":
        if payload.get("consentRequired") is True:
            if (set(payload) != {"consentRequired", "provider", "linkGeneration"}
                    or payload["provider"] not in _PROVIDERS
                    or type(payload["linkGeneration"]) is not int or payload["linkGeneration"] < 1):
                raise ValueError("Invalid browser consent response")
        else:
            if (set(payload) != {"consentRequired", "taskId", "status", "browserSessionId", "expiresAt"}
                    or payload["consentRequired"] is not False or payload["status"] not in _STATES):
                raise ValueError("Invalid browser task receipt")
            _uuid(payload["taskId"])
            _uuid(payload["browserSessionId"])
            _expiry(payload["expiresAt"])
    elif command == "review-link":
        if (set(payload) != {"token", "expiresAt"} or not isinstance(payload["token"], str)
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["token"]) is None):
            raise ValueError("Invalid browser handoff response")
        _expiry(payload["expiresAt"])
    elif command == "end-session":
        if set(payload) != {"ended"} or payload["ended"] is not True:
            raise ValueError("Invalid browser session end response")
    elif command == "ack":
        if set(payload) != {"acknowledged"} or payload["acknowledged"] is not True:
            raise ValueError("Invalid browser acknowledgment response")
    elif command == "stop":
        if set(payload) != {"status"} or payload["status"] not in _STATES:
            raise ValueError("Invalid browser stop response")
    elif command == "status":
        required = {"taskId", "status", "channel", "scopeId", "createdAt", "expiresAt", "cursor", "nextCursor"}
        allowed = required | {"parentSessionId", "sourceLinkGeneration", "result", "terminalReason",
                              "events", "resetRequired", "waitingForUserType", "review"}
        if (not required <= set(payload) or set(payload) - allowed
                or payload["status"] not in _STATES or payload["channel"] != "discord_dm"
                or payload["scopeId"] != "public_research"
                or any(type(payload[key]) is not int or payload[key] < 0 for key in ("cursor", "nextCursor"))
                or (payload.get("result") is not None and payload["status"] not in {"completed", "partial"})
                or payload.get("waitingForUserType") not in {None, "action_review", "local_takeover"}):
            raise ValueError("Invalid Discord browser task view")
        _uuid(payload["taskId"])
        _expiry(payload["createdAt"])
        _expiry(payload["expiresAt"])
    return payload


class DiscordBrowserBridge:
    """Dedicated bridge bearer transport, without any broker or worker endpoint."""
    def __init__(self, client):
        self.client = client

    async def request(self, command, user_id, **fields):
        if command not in {"start", "status", "stop", "ack", "review-link", "end-session"}:
            raise ValueError("Unknown browser bridge operation")
        response = await self.client.post(
            f"/api/browser-tasks/bridge/discord/{command}",
            json={"discordUserId": user_id, **fields},
        )
        response.raise_for_status()
        return validate_bridge_response(command, response.json())


@dataclass
class PendingConsent:
    user_id: str
    task_id: str
    goal: str | None
    provider: str
    generation: int
    deadline: float
    view: discord.ui.View | None = None
    completed: bool = False
    expiry_timer: asyncio.TimerHandle | None = None


class ProviderConsentView(discord.ui.View):
    def __init__(self, bot, pending):
        super().__init__(timeout=60)
        self.bot = bot
        self.pending = pending
        button = discord.ui.Button(label=f"Start this browser session with {pending.provider}",
                                   style=discord.ButtonStyle.primary)
        button.callback = self.confirm
        self.add_item(button)

    async def confirm(self, interaction):
        await self.bot._run(interaction, "browser_consent", self.pending)

    async def on_timeout(self):
        self.bot.browser_commands.discard(self.pending)


class DiscordBrowserCommands:
    def __init__(self, bot, pwa_origin):
        self.bot = bot
        self.pwa_origin = validate_pwa_origin(pwa_origin)
        self.pending: dict[str, PendingConsent] = {}
        self._reset_generation = 0
        self._source_lock = asyncio.Lock()

    def discard(self, pending):
        if self.pending.get(pending.task_id) is pending:
            self.pending.pop(pending.task_id)
        pending.goal = None
        if pending.expiry_timer:
            pending.expiry_timer.cancel()
        if pending.view:
            pending.view.stop()

    def invalidate(self, user_id=None):
        self._reset_generation += 1
        for pending in list(self.pending.values()):
            if user_id is None or pending.user_id == user_id:
                self.discard(pending)

    async def end_session(self, user_id):
        self.invalidate(user_id)
        # A prior consent request must finish before its source is ended. Core's
        # bridge start carries link generation, but cannot identify a bot reset.
        async with self._source_lock:
            await self.bot.core.browser.request("end-session", user_id)

    async def start_request(self, bridge, user_id, generation, *, pending=None, **fields):
        async with self._source_lock:
            if self.bot._closed or generation != self._reset_generation:
                return None
            operation = "start"
            if pending is not None:
                # Keep only the mutable intent reference while waiting for the
                # lock; expiry must erase the sole pending copy of the goal.
                if (pending.user_id != user_id or self.pending.get(pending.task_id) is not pending
                        or time.monotonic() >= pending.deadline):
                    self.discard(pending)
                    return None
                if pending.completed:
                    operation = "status"
                    fields = {"taskId": pending.task_id}
                elif pending.goal is None:
                    self.discard(pending)
                    return None
                else:
                    fields = {"taskId": pending.task_id, "goal": pending.goal,
                              "providerConsent": pending.provider,
                              "expectedLinkGeneration": pending.generation}
            response = await bridge.request(operation, user_id, **fields)
            if self.bot._closed or generation != self._reset_generation:
                return None
            return response

    async def run(self, interaction, command, value):
        user_id = str(interaction.user.id)
        reset_generation = self._reset_generation
        if not self.pwa_origin and command in {"browser_start", "browser_consent"}:
            return "Browser handoff is unavailable. Configure the HTTPS phone PWA origin.", None
        bridge = self.bot.core.browser
        if command == "browser_start":
            if (not isinstance(value, str) or not value.strip() or len(value) > 4000
                    or _OVERRIDE.search(value)):
                return "Enter a public-research goal of 1–4000 characters without identity or configuration overrides.", None
            interaction_id = interaction.id
            if type(interaction_id) is not int or interaction_id <= 0:
                raise ValueError("Invalid Discord interaction ID")
            task_id = str(uuid5(_NAMESPACE, f"discord-browser:{user_id}:{interaction_id}"))
            existing = self.pending.get(task_id)
            if existing and existing.deadline > time.monotonic() and not existing.completed:
                if existing.goal != value.strip():
                    raise ValueError("Conflicting browser interaction redelivery")
                return self.consent_message(existing), existing.view
            if existing:
                self.discard(existing)
            response = await self.start_request(bridge, user_id, reset_generation,
                                                taskId=task_id, goal=value.strip())
            if response is None:
                return "This browser request ended. Use /browser start again.", None
            if response["consentRequired"]:
                pending = PendingConsent(user_id, task_id, value.strip(), response["provider"],
                                         response["linkGeneration"], time.monotonic() + 60)
                pending.view = ProviderConsentView(self.bot, pending)
                self.pending[task_id] = pending
                pending.expiry_timer = asyncio.get_running_loop().call_later(60, self.discard, pending)
                return self.consent_message(pending), pending.view
            if response["taskId"] != task_id:
                raise ValueError("Browser receipt task mismatch")
            return await self.receipt(bridge, user_id, response), None
        if command == "browser_consent":
            pending = value
            if (not isinstance(pending, PendingConsent) or pending.user_id != user_id
                    or self.pending.get(pending.task_id) is not pending):
                return "This browser consent belongs to another request or has expired.", None
            if time.monotonic() >= pending.deadline:
                self.discard(pending)
                return "Browser consent expired. Use /browser start again.", None
            if pending.completed:
                response = await bridge.request("status", user_id, taskId=pending.task_id)
                if response["taskId"] != pending.task_id:
                    raise ValueError("Browser status task mismatch")
                return await self.receipt(bridge, user_id, response), None
            if pending.goal is None:
                raise ValueError("Browser intent is unavailable")
            response = await self.start_request(bridge, user_id, reset_generation,
                                                pending=pending)
            if response is None:
                return "Browser consent ended or expired. Use /browser start again.", None
            if response.get("consentRequired"):
                self.discard(pending)
                return "The browser provider or link changed. Use /browser start for fresh consent.", None
            if response["taskId"] != pending.task_id:
                raise ValueError("Browser receipt task mismatch")
            pending.goal = None
            pending.completed = True
            message = await self.receipt(bridge, user_id, response)
            return message, None
        if command in {"browser_status", "browser_stop", "browser_clear"}:
            try:
                task_id = _uuid(value)
            except (ValueError, TypeError, AttributeError):
                return "Enter the browser task UUID from its receipt.", None
            operation = {"browser_status": "status", "browser_stop": "stop", "browser_clear": "ack"}[command]
            response = await bridge.request(operation, user_id, taskId=task_id)
            if operation == "status":
                if response["taskId"] != task_id:
                    raise ValueError("Browser status task mismatch")
                return await self.receipt(bridge, user_id, response), None
            if operation == "stop":
                return f"Browser task {task_id}: {response['status']}.", None
            return f"Browser task {task_id} acknowledged and cleared.", None
        raise ValueError("Unknown browser command")

    @staticmethod
    def consent_message(pending):
        return (f"Public browser research uses {pending.provider}. Confirm this request within 60 seconds. "
                "This consent starts the browser session; site actions require separate review in the phone PWA.")

    async def receipt(self, bridge, user_id, response):
        message = f"Browser task {response['taskId']}: {response['status']}. Expires {response['expiresAt']}."
        if response["status"] == "queued":
            message += " Queued in Core until the laptop is available."
        if not self.pwa_origin:
            return message + " Details unavailable; configure the HTTPS phone PWA origin."
        try:
            link = await bridge.request("review-link", user_id, taskId=response["taskId"])
        except httpx.HTTPError:
            return message + " Details unavailable; retry /browser status with this task ID."
        return message + f" Open details: {self.pwa_origin}/browser-tasks/review#handoff={link['token']}"

    @staticmethod
    def error_message(exc, command):
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code in {401, 403, 404, 410}:
                return "This browser request is no longer accessible. Check your link and start a new request."
            if exc.response.status_code == 409:
                if command == "browser_clear":
                    return "Only a finished browser task can be cleared. Check /browser status."
                return "The browser session or request changed. Check /browser status or start again."
            if exc.response.status_code == 429:
                return "The Core browser queue is busy. Please try again later."
        return "Browser service unavailable right now. Please try again."
