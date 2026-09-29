"""DM browser commands at the Discord interaction and Core HTTP boundaries."""
import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from integrations.discord_phone_bot import DiscordCoreClient, DiscordPhoneBot


USER = 123456789012345678
SESSION = "9b90f1c9-8d9e-44e5-b042-851955014ead"
EXPIRY = "2026-10-01T00:00:00+00:00"


class Interaction:
    def __init__(self, *, user=USER, guild=None, interaction_id=111):
        self.user = SimpleNamespace(id=user)
        self.guild_id = guild
        self.id = interaction_id
        self.events = []
        self.response = SimpleNamespace(defer=self.defer)

    async def defer(self, **kwargs):
        self.events.append(("defer", kwargs))

    async def edit_original_response(self, **kwargs):
        self.events.append(("edit", kwargs))

    def is_expired(self):
        return False


class CoreServer:
    def __init__(self):
        self.requests = []
        self.tasks = {}
        self.generation = 1
        self.consent = False
        self.linked = True
        self.closed_session = False

    def __call__(self, request):
        payload = json.loads(request.content)
        command = request.url.path.rsplit("/", 1)[-1]
        self.requests.append((request.url.path, payload, request.headers))
        if command == "verify-link":
            return httpx.Response(200, json={"linked": True, "ownerId": "owner-a"}
                                  if self.linked else {"linked": False})
        if command == "start":
            if payload.get("expectedLinkGeneration", self.generation) != self.generation:
                return httpx.Response(409)
            if not self.consent and payload.get("providerConsent") != "groq":
                return httpx.Response(200, json={"consentRequired": True, "provider": "groq",
                                               "linkGeneration": self.generation})
            self.consent = True
            task = {"consentRequired": False, "taskId": payload["taskId"], "status": "queued",
                    "browserSessionId": SESSION, "expiresAt": EXPIRY}
            self.tasks.setdefault(payload["taskId"], task)
            return httpx.Response(200, json=task)
        if command == "review-link":
            if self.closed_session:
                return httpx.Response(404)
            return httpx.Response(200, json={"token": "A" * 43, "expiresAt": EXPIRY})
        if command == "end-session":
            self.closed_session = True
            self.consent = False
            for task in self.tasks.values():
                task["status"] = "cancelled"
            return httpx.Response(200, json={"ended": True})
        if command in {"status", "stop", "ack"}:
            task_id = payload["taskId"]
            if self.closed_session or task_id not in self.tasks:
                return httpx.Response(404)
            if command == "stop":
                self.tasks[task_id]["status"] = "cancelled"
                return httpx.Response(200, json={"status": "cancelled"})
            if command == "ack":
                if self.tasks[task_id]["status"] == "queued":
                    return httpx.Response(409)
                return httpx.Response(200, json={"acknowledged": True})
            return httpx.Response(200, json={"taskId": task_id, "status": self.tasks[task_id]["status"],
                "channel": "discord_dm", "scopeId": "public_research", "createdAt": EXPIRY,
                "expiresAt": EXPIRY, "parentSessionId": None, "sourceLinkGeneration": self.generation,
                "result": None, "terminalReason": None, "events": [], "cursor": 0, "nextCursor": 0,
                "resetRequired": False, "waitingForUserType": None, "review": None})
        raise AssertionError(f"Unexpected route {request.url.path}")


def build(server, **kwargs):
    core = DiscordCoreClient("https://core.example", "bridge-secret",
                             transport=httpx.MockTransport(server))
    handler = SimpleNamespace(clear_session=lambda *args: None)
    return DiscordPhoneBot(core, handler, owner_id="owner-a", token="bot-secret",
                           application_id=USER, pwa_origin=kwargs.pop("pwa_origin", "https://phone.example"), **kwargs)


def test_browser_group_is_registered_dm_only_without_sync():
    server = CoreServer()
    bot = build(server)
    group = bot.tree.get_command("browser")
    assert {command.name for command in group.commands} == {"start", "status", "stop", "clear"}
    assert not group.allowed_contexts.guild and group.allowed_contexts.dm_channel
    assert bot.client._sync_commands is False
    assert server.requests == []


def test_first_start_waits_for_current_user_provider_consent_then_uses_core_queue():
    async def run():
        server = CoreServer()
        bot = build(server)
        original = Interaction()
        await bot.handle_browser_start(original, "Read the official docs")
        assert server.tasks == {}
        view = original.events[-1][1]["view"]
        assert view.timeout == 60
        assert "groq" in view.children[0].label
        await view.children[0].callback(Interaction(interaction_id=222))
        assert len(server.tasks) == 1
        request = next(payload for path, payload, _ in server.requests
                       if path.endswith("/start") and payload.get("providerConsent"))
        assert request["providerConsent"] == "groq"
        assert request["expectedLinkGeneration"] == 1
        assert request["discordUserId"] == str(USER)
        UUID(request["taskId"])
        assert set(request) == {"discordUserId", "taskId", "goal", "providerConsent", "expectedLinkGeneration"}
        assert all(path.startswith(("/api/browser-tasks/bridge/discord/",
                                    "/api/phone-chat/worker/discord/"))
                   for path, _, _ in server.requests)
        await bot.close()
    asyncio.run(run())


def test_status_stop_and_terminal_clear_use_task_scoped_core_routes_and_fragment_handoff():
    async def run():
        server = CoreServer()
        server.consent = True
        bot = build(server)
        start = Interaction()
        await bot.handle_browser_start(start, "Read docs")
        task_id = next(iter(server.tasks))
        status = Interaction(interaction_id=112)
        await bot.handle_browser_status(status, task_id)
        assert f"https://phone.example/browser-tasks/review#handoff={'A' * 43}" in status.events[-1][1]["content"]
        stop = Interaction(interaction_id=113)
        await bot.handle_browser_stop(stop, task_id)
        assert "cancelled" in stop.events[-1][1]["content"]
        clear = Interaction(interaction_id=114)
        await bot.handle_browser_clear(clear, task_id)
        assert "cleared" in clear.events[-1][1]["content"]
        calls = [(path.rsplit("/", 1)[-1], body) for path, body, _ in server.requests]
        for command in ("status", "stop", "ack", "review-link"):
            assert (command, {"discordUserId": str(USER), "taskId": task_id}) in calls
        assert all(headers["authorization"] == "Bearer bridge-secret" for _, _, headers in server.requests)
        assert all(event[1]["allowed_mentions"].everyone is False for event in status.events if event[0] == "edit")
        await bot.close()
    asyncio.run(run())


def test_newchat_ends_browser_session_and_old_task_status_is_inaccessible():
    async def run():
        server = CoreServer()
        server.consent = True
        bot = build(server)
        await bot.handle_browser_start(Interaction(), "Read docs")
        task_id = next(iter(server.tasks))
        newchat = Interaction(interaction_id=112)
        await bot.handle_newchat(newchat)
        assert server.closed_session is True
        assert "cleared" in newchat.events[-1][1]["content"]
        status = Interaction(interaction_id=113)
        await bot.handle_browser_status(status, task_id)
        assert "no longer accessible" in status.events[-1][1]["content"]
        assert not any("handoff=" in event[1]["content"] for event in status.events if event[0] == "edit")
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("command,value", [("start", "Read docs"), ("status", SESSION),
                                         ("stop", SESSION), ("clear", SESSION)])
def test_guild_browser_commands_never_contact_core(command, value):
    async def run():
        server = CoreServer()
        bot = build(server)
        interaction = Interaction(guild=123)
        await getattr(bot, f"handle_browser_{command}")(interaction, value)
        assert server.requests == []
        assert "DM" in interaction.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("command,value", [("start", "Read docs"), ("status", SESSION),
                                         ("stop", SESSION), ("clear", SESSION)])
def test_unlinked_browser_commands_never_contact_queue(command, value):
    async def run():
        server = CoreServer()
        server.linked = False
        bot = build(server)
        interaction = Interaction()
        await getattr(bot, f"handle_browser_{command}")(interaction, value)
        assert len(server.requests) == 1
        assert "/link" in interaction.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


def test_duplicate_interaction_and_consent_click_return_one_durable_task():
    async def run():
        server = CoreServer()
        bot = build(server)
        original = Interaction()
        await bot.handle_browser_start(original, "Read docs")
        view = original.events[-1][1]["view"]
        await bot.handle_browser_start(Interaction(), "Read docs")
        await view.children[0].callback(Interaction(interaction_id=222))
        repeated_click = Interaction(interaction_id=333)
        await view.children[0].callback(repeated_click)
        assert "queued" in repeated_click.events[-1][1]["content"]
        await bot.handle_browser_start(Interaction(), "Read docs")
        assert len(server.tasks) == 1
        await bot.close()
        restarted = build(server)
        await restarted.handle_browser_start(Interaction(), "Read docs")
        assert len(server.tasks) == 1
        await restarted.close()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["user", "guild", "expired", "relinked", "newchat", "shutdown"])
def test_consent_is_bound_to_user_dm_generation_intent_and_sixty_seconds(change, monkeypatch):
    async def run():
        server = CoreServer()
        bot = build(server)
        original = Interaction()
        await bot.handle_browser_start(original, "Read private research docs")
        view = original.events[-1][1]["view"]
        click = Interaction(interaction_id=222)
        if change == "user":
            click.user.id = USER + 1
        elif change == "guild":
            click.guild_id = 123
        elif change == "expired":
            import integrations.discord_browser_commands as commands
            monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: float("inf")))
        elif change == "relinked":
            server.generation = 2
        elif change == "newchat":
            await bot.handle_newchat(Interaction(interaction_id=333))
        elif change == "shutdown":
            await bot.close()
        await view.children[0].callback(click)
        assert server.tasks == {}
        if change in {"expired", "newchat", "shutdown"}:
            assert view.pending.goal is None
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("goal", ["", "x" * 4001, {"ownerId": "other"},
    '{"ownerId":"other","goal":"read docs"}', "provider=gemini; read docs",
    "laptopId: other", "profile_id=work", "origin=https://other.example", "path=C:/secret"])
def test_goal_cannot_supply_identity_or_configuration(goal):
    async def run():
        server = CoreServer()
        bot = build(server)
        await bot.handle_browser_start(Interaction(), goal)
        assert not any(path.endswith("/start") for path, _, _ in server.requests)
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("availability", ["busy", "offline"])
def test_unavailable_laptop_keeps_one_task_in_core_queue(availability):
    async def run():
        server = CoreServer()
        server.consent = True
        server.availability = availability
        bot = build(server)
        interaction = Interaction()
        await bot.handle_browser_start(interaction, "Read docs")
        assert len(server.tasks) == 1
        assert "Queued in Core until the laptop is available" in interaction.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


def test_core_network_failure_is_generic_and_never_logs_goal_or_credentials(caplog):
    async def run():
        server = CoreServer()
        def unavailable(request):
            if request.url.path.endswith("/start"):
                raise httpx.ConnectError("secret research and bridge-secret")
            return server(request)
        bot = build(unavailable)
        interaction = Interaction()
        await bot.handle_browser_start(interaction, "secret research")
        assert "unavailable" in interaction.events[-1][1]["content"]
        assert "secret research" not in caplog.text
        assert "bridge-secret" not in caplog.text
        assert server.tasks == {}
        await bot.close()
    asyncio.run(run())


def test_active_task_clear_is_rejected_by_core():
    async def run():
        server = CoreServer()
        server.consent = True
        bot = build(server)
        await bot.handle_browser_start(Interaction(), "Read docs")
        clear = Interaction(interaction_id=112)
        await bot.handle_browser_clear(clear, next(iter(server.tasks)))
        assert "Only a finished" in clear.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


def test_timeout_discards_pending_goal_without_any_core_start():
    async def run():
        server = CoreServer()
        bot = build(server)
        original = Interaction()
        await bot.handle_browser_start(original, "Read docs")
        view = original.events[-1][1]["view"]
        await view.on_timeout()
        assert view.pending.goal is None
        await view.children[0].callback(Interaction(interaction_id=222))
        assert server.tasks == {}
        await bot.close()
    asyncio.run(run())


def test_provider_change_requires_a_new_explicit_request():
    async def run():
        server = CoreServer()
        def changed_provider(request):
            body = json.loads(request.content)
            if request.url.path.endswith("/start") and body.get("providerConsent"):
                return httpx.Response(200, json={"consentRequired": True, "provider": "gemini", "linkGeneration": 1})
            return server(request)
        bot = build(changed_provider)
        original = Interaction()
        await bot.handle_browser_start(original, "Read docs")
        view = original.events[-1][1]["view"]
        click = Interaction(interaction_id=222)
        await view.children[0].callback(click)
        assert "fresh consent" in click.events[-1][1]["content"]
        assert server.tasks == {}
        assert view.pending.goal is None
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("code", [403, 404, 410])
def test_relinked_or_stale_source_status_never_creates_handoff(code):
    async def run():
        server = CoreServer()
        def stale_source(request):
            if request.url.path.endswith("/status"):
                return httpx.Response(code)
            return server(request)
        bot = build(stale_source)
        interaction = Interaction()
        await bot.handle_browser_status(interaction, SESSION)
        assert "no longer accessible" in interaction.events[-1][1]["content"]
        assert not any(path.endswith("review-link") for path, _, _ in server.requests)
        await bot.close()
    asyncio.run(run())


def test_lost_handoff_response_preserves_durable_task_receipt():
    async def run():
        server = CoreServer()
        server.consent = True
        def lost_response(request):
            if request.url.path.endswith("review-link"):
                raise httpx.ConnectError("private response details")
            return server(request)
        bot = build(lost_response)
        interaction = Interaction()
        await bot.handle_browser_start(interaction, "Read docs")
        task_id = next(iter(server.tasks))
        assert task_id in interaction.events[-1][1]["content"]
        assert "retry /browser status" in interaction.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("payload", [
    {"consentRequired": True, "provider": "unapproved", "linkGeneration": 1},
    {"consentRequired": True, "provider": "groq", "linkGeneration": True},
    {"consentRequired": True, "provider": "groq", "linkGeneration": 1, "ownerId": "override"},
    {"consentRequired": False, "taskId": SESSION, "status": "queued", "browserSessionId": SESSION, "expiresAt": EXPIRY},
])
def test_malformed_or_wrong_task_core_start_receipt_cannot_create_consent_or_handoff(payload):
    async def run():
        server = CoreServer()
        def invalid_response(request):
            if request.url.path.endswith("/start"):
                return httpx.Response(200, json=payload)
            return server(request)
        bot = build(invalid_response)
        interaction = Interaction()
        await bot.handle_browser_start(interaction, "Read docs")
        assert "unavailable" in interaction.events[-1][1]["content"]
        assert "view" not in interaction.events[-1][1]
        assert not any(path.endswith("review-link") for path, _, _ in server.requests)
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("token", ["x" * 42, "https://other.example/", "A" * 43 + "\n"])
def test_malformed_handoff_is_not_published(token):
    async def run():
        server = CoreServer()
        server.consent = True
        def invalid_link(request):
            if request.url.path.endswith("review-link"):
                return httpx.Response(200, json={"token": token, "expiresAt": EXPIRY})
            return server(request)
        bot = build(invalid_link)
        interaction = Interaction()
        await bot.handle_browser_start(interaction, "Read docs")
        assert "handoff=" not in interaction.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("origin", ["http://phone.example", "https://user@phone.example",
    "https://phone.example/path", "https://phone.example?token=x", "https://phone.example#token=x",
    "https://phone.example:invalid", "https://phone.example\n"])
def test_handoff_configuration_requires_an_https_origin(origin):
    from integrations.discord_browser_commands import validate_pwa_origin
    with pytest.raises(ValueError):
        validate_pwa_origin(origin)


def test_command_setup_does_not_automatically_sync():
    async def run():
        server = CoreServer()
        bot = build(server)
        async def forbidden_sync(*args, **kwargs):
            raise AssertionError("Slash synchronization is staging-only")
        bot.tree.sync = forbidden_sync
        await bot.client.setup_hook()
        await bot.close()
    asyncio.run(run())


def test_status_reply_never_contains_detailed_page_results_or_action_approval():
    async def run():
        server = CoreServer()
        server.consent = True
        def detailed_status(request):
            response = server(request)
            if request.url.path.endswith("/status"):
                payload = json.loads(response.content)
                payload.update(status="completed", result={"summary": "@everyone private long page result"})
                return httpx.Response(200, json=payload)
            return response
        bot = build(detailed_status)
        await bot.handle_browser_start(Interaction(), "Read docs")
        interaction = Interaction(interaction_id=112)
        await bot.handle_browser_status(interaction, next(iter(server.tasks)))
        assert "private long page result" not in interaction.events[-1][1]["content"]
        assert "Open details" in interaction.events[-1][1]["content"]
        assert not any("reviews" in path for path, _, _ in server.requests)
        await bot.close()
    asyncio.run(run())


def test_missing_handoff_origin_does_not_block_stop_or_terminal_clear():
    async def run():
        server = CoreServer()
        server.tasks[SESSION] = {"status": "queued"}
        bot = build(server, pwa_origin="")
        stop = Interaction()
        await bot.handle_browser_stop(stop, SESSION)
        assert "cancelled" in stop.events[-1][1]["content"]
        clear = Interaction(interaction_id=112)
        await bot.handle_browser_clear(clear, SESSION)
        assert "cleared" in clear.events[-1][1]["content"]
        await bot.close()
    asyncio.run(run())


def test_consent_goal_expiry_is_fixed_even_when_discord_refreshes_view_timeout(monkeypatch):
    async def run():
        scheduled = []
        loop = asyncio.get_running_loop()
        original_schedule = loop.call_later
        def capture_schedule(delay, callback, *args, **kwargs):
            if delay == 60:
                scheduled.append((callback, args))
            return original_schedule(delay, callback, *args, **kwargs)
        monkeypatch.setattr(loop, "call_later", capture_schedule)
        server = CoreServer()
        bot = build(server)
        original = Interaction()
        await bot.handle_browser_start(original, "Read docs")
        view = original.events[-1][1]["view"]
        assert len(scheduled) == 1
        view.timeout = 120  # Discord's sliding UI timeout cannot extend intent retention.
        callback, args = scheduled[0]
        callback(*args)
        assert view.pending.goal is None
        assert server.tasks == {}
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["newchat", "shutdown"])
def test_late_consent_response_cannot_restore_an_ended_intent(boundary):
    async def run():
        server = CoreServer()
        started = asyncio.Event()
        release = asyncio.Event()
        async def delayed_response(request):
            if request.url.path.endswith("/start"):
                started.set()
                await release.wait()
            return server(request)
        bot = build(delayed_response)
        original = Interaction()
        pending = asyncio.create_task(bot.handle_browser_start(original, "Read docs"))
        await asyncio.wait_for(started.wait(), 1)
        ending = None
        if boundary == "newchat":
            ending = asyncio.create_task(bot.handle_newchat(Interaction(interaction_id=112)))
            await asyncio.sleep(0)
        else:
            await bot.close()
        release.set()
        await pending
        if ending:
            await ending
        assert not any("view" in event[1] for event in original.events if event[0] == "edit")
        assert bot.browser_commands.pending == {}
        assert server.tasks == {}
        await bot.close()
    asyncio.run(run())


def test_newchat_end_session_is_ordered_after_an_inflight_consent_start():
    async def run():
        server = CoreServer()
        started = asyncio.Event()
        release = asyncio.Event()
        async def delayed_consent(request):
            body = json.loads(request.content)
            if request.url.path.endswith("/start") and body.get("providerConsent"):
                started.set()
                await release.wait()
            return server(request)
        bot = build(delayed_consent)
        original = Interaction()
        await bot.handle_browser_start(original, "Read docs")
        view = original.events[-1][1]["view"]
        clicking = asyncio.create_task(view.children[0].callback(Interaction(interaction_id=222)))
        await asyncio.wait_for(started.wait(), 1)
        ending = asyncio.create_task(bot.handle_newchat(Interaction(interaction_id=333)))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(clicking, ending)
        commands = [path.rsplit("/", 1)[-1] for path, _, _ in server.requests]
        assert commands[-2:] == ["start", "end-session"]
        assert len(server.tasks) == 1
        assert next(iter(server.tasks.values()))["status"] == "cancelled"
        assert view.pending.goal is None
        assert bot.browser_commands.pending == {}
        await bot.close()
    asyncio.run(run())


@pytest.mark.parametrize("expiry", ["timer", "deadline"])
def test_expired_consent_waiting_for_source_lock_never_reaches_core(expiry, monkeypatch):
    async def run():
        import integrations.discord_browser_commands as commands
        now = [100.0]
        monkeypatch.setattr(commands, "time", SimpleNamespace(monotonic=lambda: now[0]))
        server = CoreServer()
        started = asyncio.Event()
        release = asyncio.Event()
        async def delayed_other_start(request):
            body = json.loads(request.content)
            if request.url.path.endswith("/start") and body.get("goal") == "Hold the source lock":
                started.set()
                await release.wait()
            return server(request)
        bot = build(delayed_other_start)
        original = Interaction()
        await bot.handle_browser_start(original, "Research that must expire")
        view = original.events[-1][1]["view"]
        blocker = asyncio.create_task(bot.handle_browser_start(
            Interaction(interaction_id=112), "Hold the source lock"))
        await asyncio.wait_for(started.wait(), 1)
        click = Interaction(interaction_id=222)
        clicking = asyncio.create_task(view.children[0].callback(click))
        await asyncio.sleep(0)
        assert click.events[0][0] == "defer"
        assert not clicking.done()
        if expiry == "timer":
            await view.on_timeout()
            assert view.pending.goal is None
        else:
            now[0] = 161.0
        release.set()
        await asyncio.gather(blocker, clicking)
        assert server.tasks == {}
        assert not any(body.get("providerConsent") for path, body, _ in server.requests
                       if path.endswith("/start"))
        assert view.pending.goal is None
        await bot.close()
    asyncio.run(run())
