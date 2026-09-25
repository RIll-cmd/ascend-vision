# Unified Assistant Conversation Design

**Date:** 2026-09-25  
**Milestone:** First deliverable in the AI Assistant Future Features Audit and Implementation Roadmap

## Goal

Voice and the local dashboard must use one conversational answer service. A typed dashboard message must produce the answer that Vision generated, rather than remain in an unclaimed inbox or receive a generic acknowledgement. This milestone prepares a stable entry point for memory, Hub status, and remote phone chat.

## Current state

`main.py` sends conversational voice text to `FeedbackService.submit_chat`, which queues generation and speech. The generated text does not return to the caller. The dashboard writes a message to `ChatIpcQueue`, but `main.py` never starts `ChatRuntimeBridge`. The bridge's handler contract accepts a string, yet an empty or missing result currently becomes the misleading success reply “Vision received your message.” `ConversationContext` construction lives inside the voice route and reads the main `Database` connection.

## Architecture

```text
Voice transcript ──> existing voice routing ──> FeedbackService speech queue ──┐
                                                                            ├──> AssistantService.respond()
Dashboard POST ──> ChatIpcQueue ──> ChatRuntimeBridge ──> dashboard handler ─┘         │
                                                                                       ▼
                                                                           LLMRoaster or OfflineRoaster
                                                                                       │
                                                                                       ▼
                                                                                AssistantReply.text
```

`AssistantService` owns conversational generation and returns `AssistantReply(text, source)`. It lazily selects the existing `LLMRoaster` when an available provider key exists and `OfflineRoaster` otherwise. The service serializes generation so the voice worker and dashboard worker do not use one provider client concurrently. Provider exceptions and empty provider replies use the existing offline chat fallback. If even that fallback fails, the caller receives an error; private exception text is not shown to the user.

The voice route keeps its current command, mute, cooldown, speech, and cancellation behavior. `FeedbackService` delegates only its `_ChatJob` generation to the bound `AssistantService` and speaks the returned text. Existing alert and expression generation remain in `FeedbackService`.

The dashboard handler calls the same service synchronously and returns `AssistantReply.text` to `ChatRuntimeBridge`. The dashboard does not speak a response. `main.py` starts the bridge after its handlers and resources are initialized, using the same `chat_ipc.db` path as `dashboard.py`. It stops the bridge through the existing resource stack. A missing handler response is a safe `error` reply, not a success acknowledgement.

## Session context and concurrency

Both channels receive a `ConversationContext` built from the current session ID, mode, elapsed time, and event counts. The context builder reads event counts through a separate short-lived SQLite connection. This prevents the dashboard worker from sharing the main runtime's SQLite connection with camera and session writes. A missing or temporarily unreadable count query yields empty counts while chat remains available.

The context contains counts and mode only. No frames, microphone audio, provider credentials, or Core tokens enter chat prompts.

## Interfaces and files

| File | Responsibility |
|---|---|
| `assistant/service.py` | `AssistantReply` and synchronized `AssistantService.respond(text, context, max_words)`. |
| `assistant/context.py` | `build_conversation_context(text, session_id, mode, database_path, started_at)`. |
| `feedback.py` | Bind the service; use it to generate voice chat text before speech. |
| `integrations/chat_runtime.py` | Publish a safe error for missing or empty handler text. |
| `main.py` | Construct the service, build context for both channels, and start/stop the dashboard bridge. |
| `tests/test_assistant_service.py` | Model and offline answer, validation, provider failure, and concurrency behavior. |
| `tests/test_assistant_context.py` | Event counts and unreadable database fallback. |
| `tests/test_chat_runtime.py` | Queue response and missing-answer behavior. |
| `tests/test_main.py` | Synthetic runtime integration from queued dashboard message to actual reply. |
| `tests/test_voice_commands.py` | Speech output from the same bound assistant service. |

`AssistantService.respond` returns a typed answer for a valid, nonempty user message. It does not execute tools or Core actions. Dashboard V1 processes ordinary conversation only; action routing and explicit confirmation through dashboard require their own later design. The current local dashboard's request validation, loopback restrictions, IPC schema, and polling UI remain in use.

## Failure and lifecycle behavior

- No provider key: use `OfflineRoaster`; dashboard receives its text.
- Provider error or empty answer: use the existing offline chat fallback and log only the exception class.
- Empty input to the service: raise `ValueError`; dashboard route already rejects empty text at enqueue.
- Assistant handler raises: the bridge writes its existing safe `error` reply.
- Handler returns no text: the bridge writes a safe `error` reply.
- Vision runtime is absent: the dashboard message stays queued and its UI continues to show “Waiting for Vision.”
- Dashboard queue cannot open: Vision logs the error class and continues camera monitoring; dashboard chat is unavailable for that run.
- Vision shutdown: stop the bridge before closing the session database and feedback service.
- Repeated start of a bridge remains an error; one runtime process owns the consumer.

## Security and scope

- No new network listener or remote endpoint is added.
- No token, API key, raw model payload, camera frame, or audio recording is persisted in the IPC queue.
- The local dashboard remains restricted to loopback.
- This milestone adds no conversational persistence or long-term memory.
- Hub status, Core mutations, MCP, skills, and phone chat remain separate milestones.

## Acceptance criteria

1. A dashboard POST through the existing route reaches a running Vision bridge and its polled reply equals the assistant's generated text.
2. A conversational voice utterance uses that same service and still speaks the generated text.
3. Provider failures produce the existing offline fallback through both channels.
4. Missing handler text produces an `error` reply rather than a success acknowledgement.
5. Dashboard context reads do not use the main `Database` connection from another thread.
6. The bridge shuts down with Vision and does not process a message after shutdown.
7. The full existing test suite remains green.
8. A dashboard queue storage failure does not stop camera monitoring.

## Follow-on milestone

Memory Foundation V1 attaches conversation storage, summaries, approved memories, retrieval, and dashboard memory controls to `AssistantService` after this reply path is verified. Hub status becomes the first typed read-only tool after the memory boundary is stable; phone chat later uses this same service through Ascend Hub.
