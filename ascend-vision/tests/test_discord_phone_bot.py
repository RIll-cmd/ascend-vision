"""Discord DM adapter boundaries; all Discord and Core traffic stays local."""
import asyncio
import logging
import threading
from types import SimpleNamespace

import httpx
import pytest


class FakeInteraction:
    def __init__(self, *, user_id=123456789012345678, guild_id=None):
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.events = []
        self.expired = False
        self.response = SimpleNamespace(defer=self.defer)

    async def defer(self, *, ephemeral, thinking):
        self.events.append(("defer", ephemeral, thinking))

    async def edit_original_response(self, *, content, allowed_mentions):
        self.events.append(("edit", content, allowed_mentions))

    def is_expired(self):
        return self.expired


class FakeCore:
    def __init__(self, events, *, owner="owner-a", linked=True):
        self.events = events
        self.owner = owner
        self.linked = linked

    async def verify_link(self, discord_user_id):
        self.events.append(("verify", discord_user_id))
        return self.owner if self.linked else None

    async def consume_link(self, code, discord_user_id):
        self.events.append(("consume", code, discord_user_id))
        return self.linked

    async def read_context(self, discord_user_id):
        self.events.append(("context", discord_user_id))
        return context_response()

    async def close(self):
        pass


def context_response():
    return {"available": False, "reason": "never_published", "deviceId": "laptop-1",
        "lastSeenAt": None, "fields": {
            name: {"value": value, "source": "none", "freshness": "unavailable",
                   "observedAt": None, "expiresAt": None, "ageSeconds": None,
                   "evidenceKind": "observation"}
            for name, value in {
                "deskPresence": "unknown", "desktopActivity": "unavailable",
                "foregroundCategory": "unknown", "focusSession": "unavailable",
                "declaredIntent": "none",
            }.items()
        }}

class FakeHandler:
    def __init__(self, events):
        self.events = events

    def handle(self, owner, channel, session, text, *, remote_context=None):
        event = ("handle", owner, channel, session, text)
        self.events.append(event if remote_context is None else (*event, remote_context))
        return SimpleNamespace(text="Private answer")

    def clear_session(self, owner, channel, session):
        self.events.append(("clear", owner, channel, session))


def bot_for(interaction, *, linked=True, owner="owner-a", **bot_options):
    from integrations.discord_phone_bot import DiscordPhoneBot

    core = FakeCore(interaction.events, linked=linked, owner=owner)
    handler = FakeHandler(interaction.events)
    return DiscordPhoneBot(core, handler, owner_id="owner-a", token="bot-secret",
                           application_id=123456789012345678, **bot_options)


def test_registers_only_dm_slash_commands_without_message_intent():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    assert {command.name for command in bot.tree.get_commands()} == {
        "ask", "status", "context", "newchat", "link",
    }
    assert bot.client.intents.message_content is False
    assert bot.client.intents.guilds is False
    assert all(not command.allowed_contexts.guild and command.allowed_contexts.dm_channel
               for command in bot.tree.get_commands())


@pytest.mark.parametrize("command,args", [
    ("handle_ask", ("private prompt",)),
    ("handle_status", ()),
    ("handle_context", ()),
    ("handle_newchat", ()),
    ("handle_link", ("A" * 43,)),
])
def test_guild_interaction_rejected_before_core_or_handler(command, args):
    interaction = FakeInteraction(guild_id=12)
    bot = bot_for(interaction)

    asyncio.run(getattr(bot, command)(interaction, *args))

    assert interaction.events[0] == ("defer", True, True)
    assert [event[0] for event in interaction.events] == ["defer", "edit"]
    assert "DM" in interaction.events[1][1]


def test_ask_defers_then_verifies_and_routes_to_separate_discord_session():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    asyncio.run(bot.handle_ask(interaction, "private prompt"))

    assert interaction.events[:3] == [
        ("defer", True, True),
        ("verify", "123456789012345678"),
        ("handle", "owner-a", "discord_dm", "123456789012345678", "private prompt"),
    ]
    assert interaction.events[3][0:2] == ("edit", "Private answer")


def test_laptop_context_question_fetches_link_scoped_snapshot_before_assistant():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    asyncio.run(bot.handle_ask(interaction, "What am I doing at my desk?"))

    assert interaction.events[1:4] == [
        ("verify", "123456789012345678"),
        ("context", "123456789012345678"),
        ("handle", "owner-a", "discord_dm", "123456789012345678",
         "What am I doing at my desk?", context_response()),
    ]


@pytest.mark.parametrize("command,args", [
    ("handle_ask", ("private prompt",)),
    ("handle_status", ()),
    ("handle_context", ()),
    ("handle_newchat", ()),
])
def test_unlinked_user_cannot_use_chat_commands(command, args):
    interaction = FakeInteraction()
    bot = bot_for(interaction, linked=False)

    asyncio.run(getattr(bot, command)(interaction, *args))

    assert [event[0] for event in interaction.events] == ["defer", "verify", "edit"]
    assert "/link" in interaction.events[-1][1]


def test_revoked_link_blocks_the_next_command_without_using_previous_session():
    interaction = FakeInteraction()
    bot = bot_for(interaction, cooldown_seconds=0)

    async def run():
        await bot.handle_ask(interaction, "before revoke")
        bot.core.linked = False
        later = FakeInteraction()
        await bot.handle_ask(later, "after revoke")
        assert [event[0] for event in later.events] == ["defer", "edit"]
        assert "/link" in later.events[-1][1]
        assert not any(event[0] == "handle" for event in later.events)
        await bot.close()

    asyncio.run(run())
    assert len([event for event in interaction.events if event[0] == "handle"]) == 1


def test_expired_pairing_code_never_reports_link_success():
    interaction = FakeInteraction()
    bot = bot_for(interaction, linked=False)

    async def run():
        await bot.handle_link(interaction, "A" * 43)
        await bot.close()

    asyncio.run(run())
    assert interaction.events[1] == ("consume", "A" * 43, "123456789012345678")
    assert "invalid or expired" in interaction.events[-1][1]
    assert "is linked" not in interaction.events[-1][1]


@pytest.mark.parametrize("command,args", [
    ("handle_ask", ("private prompt",)),
    ("handle_link", ("A" * 43,)),
])
def test_core_unavailable_fails_closed_without_leaking_exception(command, args, caplog):
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    async def unavailable(*_args):
        raise httpx.ConnectError("private prompt and bridge-secret in provider detail")

    bot.core.verify_link = unavailable
    bot.core.consume_link = unavailable
    with caplog.at_level(logging.WARNING):
        asyncio.run(getattr(bot, command)(interaction, *args))

    assert [event[0] for event in interaction.events] == ["defer", "edit"]
    assert "unavailable" in interaction.events[-1][1]
    assert "linked" not in interaction.events[-1][1]
    assert "private prompt" not in interaction.events[-1][1]
    assert "private prompt" not in caplog.text
    assert "bridge-secret" not in caplog.text


def test_status_uses_fixed_question_and_newchat_clears_only_discord_key():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    asyncio.run(bot.handle_status(interaction))
    assert interaction.events[2] == (
        "handle", "owner-a", "discord_dm", "123456789012345678",
        "What is the status of Ascend Hub AI agents?",
    )
    interaction.events.clear()
    asyncio.run(bot.handle_newchat(interaction))
    assert interaction.events[2] == (
        "clear", "owner-a", "discord_dm", "123456789012345678",
    )


def test_context_command_reads_only_after_link_verification_and_shows_unavailable_honestly():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    asyncio.run(bot.handle_context(interaction))

    assert interaction.events[1:3] == [
        ("verify", "123456789012345678"),
        ("context", "123456789012345678"),
    ]
    assert "unknown" in interaction.events[-1][1]
    assert "No laptop snapshot" in interaction.events[-1][1]


def test_link_defers_and_consumes_code_without_echoing_it():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    asyncio.run(bot.handle_link(interaction, "A" * 43))

    assert interaction.events[0] == ("defer", True, True)
    assert interaction.events[1] == ("consume", "A" * 43, "123456789012345678")
    assert "A" * 43 not in interaction.events[2][1]


@pytest.mark.parametrize("command,args", [
    ("handle_ask", ("private prompt",)),
    ("handle_link", ("A" * 43,)),
])
def test_failures_return_generic_reply_and_do_not_log_sensitive_values(command, args, caplog):
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    async def fail(*_args):
        raise RuntimeError("secret: private prompt AAAAAAAAA")

    bot.core.verify_link = fail
    bot.core.consume_link = fail
    with caplog.at_level(logging.WARNING):
        asyncio.run(getattr(bot, command)(interaction, *args))

    assert [event[0] for event in interaction.events] == ["defer", "edit"]
    assert "secret" not in interaction.events[-1][1]
    assert "private prompt" not in caplog.text
    assert "A" * 43 not in caplog.text
    assert "bot-secret" not in caplog.text


def test_core_client_uses_dedicated_bearer_and_exact_pairing_paths():
    from integrations.discord_phone_bot import DiscordCoreClient

    received = []

    def transport(request):
        received.append(request)
        if request.url.path.endswith("consume-link"):
            return httpx.Response(200, json={"linked": True})
        if request.url.path.endswith("verify-link"):
            return httpx.Response(200, json={"linked": True, "ownerId": "owner-a"})
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json=context_response())
        return httpx.Response(200, json={"linked": True, "ownerId": "owner-a"})

    async def run():
        client = DiscordCoreClient("https://core.example", "bridge-secret",
                                   transport=httpx.MockTransport(transport))
        try:
            assert await client.consume_link("A" * 43, "123456789012345678") is True
            assert await client.verify_link("123456789012345678") == "owner-a"
            assert await client.read_context("123456789012345678") == context_response()
        finally:
            await client.close()

    asyncio.run(run())
    assert [request.url.path for request in received] == [
        "/api/phone-chat/worker/discord/consume-link",
        "/api/phone-chat/worker/discord/verify-link",
        "/api/phone-chat/worker/discord/context",
    ]
    assert all(request.headers["Authorization"] == "Bearer bridge-secret" for request in received)
    assert received[0].read().decode().count("discordUserId") == 1


def test_config_fails_before_connection_when_missing_or_reused_credentials():
    from integrations.discord_phone_bot import DiscordBotSettings

    with pytest.raises(ValueError):
        DiscordBotSettings.from_environ({})
    config = {
        "ASCEND_DISCORD_BOT_TOKEN": "bot-secret",
        "ASCEND_DISCORD_APPLICATION_ID": "123456789012345678",
        "ASCEND_PHONE_CORE_URL": "https://core.example",
        "ASCEND_DISCORD_BRIDGE_TOKEN": "bridge-secret",
        "ASCEND_PHONE_OWNER_ID": "owner-a",
        "ASCEND_PHONE_WORKER_TOKEN": "worker-secret",
    }
    assert DiscordBotSettings.from_environ(config).owner_id == "owner-a"
    config["ASCEND_DISCORD_BRIDGE_TOKEN"] = "worker-secret"
    with pytest.raises(ValueError):
        DiscordBotSettings.from_environ(config)


def test_process_builder_keeps_pending_memory_proposals(tmp_path, monkeypatch):
    from dataclasses import replace
    from assistant.memory import MemoryStore
    from config import Config, StorageConfig
    from discord_bot import build_discord_bot
    from integrations.discord_phone_bot import DiscordBotSettings

    memory_path = tmp_path / "assistant_memory.db"
    store = MemoryStore(memory_path)
    store.propose("I prefer tea")
    config = replace(Config(), storage=StorageConfig(database=tmp_path / "phone_watch.db"))
    settings = DiscordBotSettings("bot-secret", 123456789012345678,
                                  "https://core.example", "bridge-secret", "owner-a")
    monkeypatch.delenv("ASCEND_STATUS_READ_CREDENTIAL", raising=False)

    bot = build_discord_bot(settings, config)

    assert [row["text"] for row in MemoryStore(memory_path).pending()] == ["I prefer tea"]
    asyncio.run(bot.close())


def test_process_exits_with_actionable_error_before_connecting_when_settings_missing(monkeypatch, caplog):
    from discord_bot import main

    for name in (
        "ASCEND_DISCORD_BOT_TOKEN", "ASCEND_DISCORD_APPLICATION_ID",
        "ASCEND_PHONE_CORE_URL", "ASCEND_DISCORD_BRIDGE_TOKEN",
        "ASCEND_PHONE_OWNER_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    with caplog.at_level(logging.ERROR):
        result = main([])

    assert result == 2
    assert "token, application ID, Core URL, bridge token, and owner ID" in caplog.text


def test_process_logging_does_not_emit_provider_exception_details(caplog):
    from discord_bot import configure_private_logging

    router_log = logging.getLogger("llm_router")
    original = router_log.level
    try:
        configure_private_logging()
        with caplog.at_level(logging.ERROR):
            router_log.error("provider error included private prompt")
        assert "private prompt" not in caplog.text
    finally:
        router_log.setLevel(original)


def test_gateway_offline_closes_the_optional_process_without_a_success_signal():
    from discord_bot import _serve
    from assistant.phone_handler import PhoneMessageHandler
    from assistant.service import AssistantReply

    class OfflineBot:
        def __init__(self):
            self.events = []

        async def start(self):
            self.events.append("start")
            raise ConnectionError("Gateway unavailable")

        async def close(self):
            self.events.append("close")

    bot = OfflineBot()
    with pytest.raises(ConnectionError, match="Gateway unavailable"):
        asyncio.run(_serve(bot))
    assert bot.events == ["start", "close"]

    class PwaAssistant:
        def respond(self, text, context=None, *, max_words=25, session_key=None):
            assert session_key == ("owner-a", "phone_pwa", "pwa-device")
            return AssistantReply("PWA still works", "offline")

        def clear_session(self, session_key):
            pass

    pwa = PhoneMessageHandler(PwaAssistant(), owner_id="owner-a")
    assert pwa.handle("owner-a", "phone_pwa", "pwa-device", "Are you there?").text == "PWA still works"


def test_shutdown_during_an_interaction_does_not_emit_a_late_success_reply():
    from integrations.discord_phone_bot import DiscordPhoneBot

    class BlockingHandler:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def handle(self, owner, channel, session, text):
            self.started.set()
            assert self.release.wait(5)
            self.finished.set()
            return SimpleNamespace(text="late private answer")

        def clear_session(self, owner, channel, session):
            pass

    async def run():
        from discord_bot import _serve

        handler = BlockingHandler()
        interaction = FakeInteraction()
        bot = DiscordPhoneBot(FakeCore(interaction.events), handler,
                              owner_id="owner-a", token="bot-secret",
                              application_id=123456789012345678)
        gateway_wait = asyncio.Event()

        async def connected_until_shutdown():
            await gateway_wait.wait()

        bot.start = connected_until_shutdown
        runner = asyncio.create_task(_serve(bot))
        pending = asyncio.create_task(bot.handle_ask(interaction, "private prompt"))
        try:
            assert await asyncio.to_thread(handler.started.wait, 1)
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
        finally:
            handler.release.set()
            assert await asyncio.to_thread(handler.finished.wait, 1)
        await pending
        assert not any(event[0] == "edit" for event in interaction.events)
        assert bot._active_owners == set()

    asyncio.run(run())


def test_closed_bot_does_not_admit_another_command():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    async def run():
        await bot.close()
        await bot.handle_ask(interaction, "private prompt")

    asyncio.run(run())
    assert interaction.events == []


def test_shutdown_while_core_verifies_does_not_start_new_handler_work():
    interaction = FakeInteraction()
    bot = bot_for(interaction)

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_verify(_discord_user_id):
            started.set()
            await release.wait()
            return "owner-a"

        bot.core.verify_link = delayed_verify
        pending = asyncio.create_task(bot.handle_ask(interaction, "private prompt"))
        await asyncio.wait_for(started.wait(), 1)
        await bot.close()
        release.set()
        await pending

    asyncio.run(run())
    assert [event[0] for event in interaction.events] == ["defer"]


def test_burst_is_rejected_and_newchat_waits_for_ask_before_clearing():
    from integrations.discord_phone_bot import DiscordPhoneBot

    class BlockingHandler:
        started = threading.Event()
        release = threading.Event()

        def __init__(self):
            self.turns = []
            self.clear_count = 0

        def handle(self, owner, channel, session, text):
            if text == "before":
                self.started.set()
                assert self.release.wait(2)
            self.turns.append(text)
            return SimpleNamespace(text=",".join(self.turns))

        def clear_session(self, owner, channel, session):
            self.clear_count += 1
            self.turns.clear()

    async def run():
        events = []
        handler = BlockingHandler()
        bot = DiscordPhoneBot(FakeCore(events), handler, owner_id="owner-a",
                              token="bot-secret", application_id=123456789012345678,
                              cooldown_seconds=0)
        first = FakeInteraction()
        first_task = asyncio.create_task(bot.handle_ask(first, "before"))
        assert await asyncio.to_thread(handler.started.wait, 1)
        bursts = [FakeInteraction() for _ in range(5)]
        await asyncio.gather(*(bot.handle_ask(item, "burst") for item in bursts))
        during = FakeInteraction()
        await bot.handle_newchat(during)
        assert handler.clear_count == 0
        assert handler.turns == []
        assert all("busy" in item.events[-1][1].lower() for item in bursts + [during])
        handler.release.set()
        await first_task
        cleared = FakeInteraction()
        await bot.handle_newchat(cleared)
        assert handler.clear_count == 1
        after = FakeInteraction()
        await bot.handle_ask(after, "after")
        assert after.events[-1][1] == "after"
        await bot.close()

    asyncio.run(run())


def test_timed_out_thread_keeps_owner_slot_until_real_work_finishes():
    from integrations.discord_phone_bot import DiscordPhoneBot

    class SlowHandler:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def handle(self, owner, channel, session, text):
            self.started.set()
            assert self.release.wait(2)
            self.finished.set()
            return SimpleNamespace(text="late reply")

        def clear_session(self, owner, channel, session):
            pass

    async def run():
        handler = SlowHandler()
        bot = DiscordPhoneBot(FakeCore([]), handler, owner_id="owner-a",
                              token="bot-secret", application_id=123456789012345678,
                              cooldown_seconds=0, work_timeout_seconds=.02)
        first = FakeInteraction()
        await bot.handle_ask(first, "slow")
        assert handler.started.is_set()
        assert "try again" in first.events[-1][1].lower()
        during = FakeInteraction()
        await bot.handle_newchat(during)
        assert "busy" in during.events[-1][1].lower()
        handler.release.set()
        assert await asyncio.to_thread(handler.finished.wait, 1)
        await asyncio.sleep(.03)
        after = FakeInteraction()
        await bot.handle_newchat(after)
        assert "cleared" in after.events[-1][1].lower()
        await bot.close()

    asyncio.run(run())


def test_completed_ask_cooldown_rejects_immediate_second_ask():
    interaction = FakeInteraction()
    bot = bot_for(interaction, cooldown_seconds=60)

    async def run():
        await bot.handle_ask(interaction, "first")
        another = FakeInteraction()
        await bot.handle_ask(another, "second")
        assert "busy" in another.events[-1][1].lower()
        assert len([event for event in interaction.events if event[0] == "handle"]) == 1
        await bot.close()

    asyncio.run(run())
