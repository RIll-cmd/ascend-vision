# Discord phone chat: staging operations

This is the optional, single-owner Discord channel for Ascend Vision. It accepts only `/ask`, `/status`, `/newchat`, and `/link` as application commands in a private Discord context. The process uses the existing phone message handler, with a separate `discord_dm` session from the PWA. It does not listen to normal messages, open an inbound HTTP port, or start the desktop and camera process.

Discord receives and processes the commands and prompts submitted in its client. Treat Discord as an external platform with its own retention and account controls. Vision's bot does not intentionally save a chat transcript; approved memories can still use Vision's local memory store under the existing memory policy. Explicit memory approval, editing, and deletion are available only in the dashboard.

## Before starting

1. Use a **staging-only** Core deployment, staging database, staging PWA preview, and a separate test Discord application. Apply the reviewed Core phone/Discord schema to staging before testing. Keep production credentials and the production Discord application out of this setup.
2. In the [Discord Developer Portal](https://discord.com/developers/applications), create an official application and bot. Configure installation for the test operator. For a bot used through the Gateway and direct DMs, grant only the `bot` and `applications.commands` OAuth scopes; do not grant guild permissions that this command-only bot does not use. A private test server shared with the bot can make its direct-DM commands available. Keep the application private during staging. Do not use a user-account token or self-bot.
3. Leave **Message Content Intent** and other privileged intents disabled. The code uses `Intents.none()` and handles application command interactions only. Discord's command contexts and an in-process guild check restrict these commands to private interactions; test direct DMs explicitly. There are no prefix commands or arbitrary message listeners.
4. Store credentials in the staging host's secret manager or protected environment, outside the repository and shell history. Set the following separately on the named service:

   | Service | Environment variable | Purpose |
   | --- | --- | --- |
   | Staging Core | `ASCEND_DISCORD_BRIDGE_TOKEN` | Authenticates the bot's consume/verify calls. |
   | Staging Core | `ASCEND_DISCORD_PAIRING_HMAC_SECRET` | Hashes pairing codes; must differ from bridge and phone-worker tokens. |
   | Staging Core | `ASCEND_PHONE_OWNER_ID` | The staging phone-chat owner identity. |
   | Vision bot | `ASCEND_DISCORD_BOT_TOKEN` | Discord bot token. |
   | Vision bot | `ASCEND_DISCORD_APPLICATION_ID` | Numeric Discord application ID. |
   | Vision bot | `ASCEND_DISCORD_BRIDGE_TOKEN` | The staging Core bridge token. |
   | Vision bot | `ASCEND_PHONE_CORE_URL` | Staging Core HTTPS origin, with no path. |
   | Vision bot | `ASCEND_PHONE_OWNER_ID` | The same configured staging owner as Core. |
   | Vision bot, optional | `ASCEND_STATUS_READ_CREDENTIAL` | Read-only Ascend Hub shelf access for `/status`. |

   Core's phone-worker token is a different credential. The bot checks that the bridge token differs from its bot token and, if present, the worker token. Configure the PWA preview to call staging Core and allow that preview origin in Core as required by the existing phone-chat deployment.

## Install and run

From the `ascend-vision` project directory, create a Python environment if needed, then install the optional dependency:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-discord.txt
.\.venv\Scripts\python.exe discord_bot.py --sync-commands
```

Run `--sync-commands` only when publishing or updating the four global slash commands is intended. After that registration, start the process normally with `.\.venv\Scripts\python.exe discord_bot.py`. Command propagation in Discord may take time. The process exits with a configuration error before connecting if a required value is missing or invalid. It does not start automatically with `main.py`.

Stop with Ctrl+C in an interactive terminal, or the process manager's normal SIGTERM/stop action. An in-flight command may not deliver a reply during shutdown. The process closes the Discord and Core clients; the PWA worker is a separate process and continues independently. Stopping the bot does not remove registered commands from Discord. If rolling back the staging feature entirely, revoke the link in the PWA, stop the bot, and remove the staging application's commands or installation through Discord's developer controls.

## Pair and verify in staging

1. Sign in to the staging PWA and register an active phone-chat device. Open its Discord linking card and generate a code. The code is shown only in the current browser component, expires after five minutes, and is consumed once. Do not paste it into logs or tickets.
2. In a direct DM with the test bot, run `/link` with the code. The bot submits the code and your Discord user ID to staging Core. Refresh the PWA link status; it should show a linked account without exposing the code or full Discord ID.
3. Run `/ask` and `/status` in the DM. `/status` asks the fixed Ascend Hub AI status question; a live shelf answer requires the separate read-only shelf credential. Confirm the Discord conversation and PWA conversation retain distinct context. `/newchat` clears only the Discord session.
4. Revoke the link in the PWA. The next `/ask`, `/status`, or `/newchat` must require linking again. An expired, replayed, or invalid code must not report success. When Core is unavailable, the bot must return a generic unavailable reply; when the bot is offline, Discord cannot receive a Vision answer. Restarting the bot does not restore a revoked link.
5. Repeat the direct-DM checks on a physical phone. Verify command visibility and replies there, then check that guild invocation is rejected and an unrelated Discord account cannot use the owner's commands. Confirm the PWA still sends and receives while the bot is stopped.

Complete these checks with staging credentials and a test Discord application before any production rollout decision. The current offline test suite uses fakes; it does not prove Discord command propagation, real Gateway delivery, Core/database transactions, or physical-phone behavior.

## Recovery and rotation

- **Bot token suspected exposed:** Stop the bot, rotate the token in the Discord Developer Portal, replace only the protected Vision bot secret, then restart. The previous token must no longer authenticate.
- **Bridge token suspected exposed:** Stop the bot, rotate `ASCEND_DISCORD_BRIDGE_TOKEN` in staging Core and Vision together, restart both services, and verify `/link` and `/ask`. Keep it distinct from the phone-worker token and HMAC secret.
- **Pairing HMAC secret suspected exposed:** Rotate it in staging Core. Previously issued codes become unusable; generate a new code. Check the current link separately, then revoke and relink if account control is in doubt.
- **Discord disconnected or Core unavailable:** Check process health and staging Core connectivity without printing request bodies or secrets. Existing PWA chat is independent. The bot verifies the Core link for each chat command and fails closed when it cannot verify it.

Keep operational logs limited to command names and exception classes. Do not copy prompt text, pairing codes, bot tokens, bridge tokens, or reply bodies into incident reports.

References: [Discord application commands](https://discord.com/developers/docs/interactions/slash-commands), [Discord interaction responses](https://discord.com/developers/docs/interactions/receiving-and-responding), and [discord.py interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html).
