# Ascend Hub Status Tool V1 Design

## Goal and scope

Vision answers spoken and dashboard questions about Ascend Hub AI status from Core's authenticated `/api/status/shelf` response. The same `AssistantService` path handles both channels. This slice adds one native read-only tool with deterministic intent routing and rendering. It does not change Core's status schema, execute actions, provide phone chat, or claim an agent finished work: the current shelf has no completion record.

## Flow

1. `AssistantService` recognizes explicit Hub/AI status questions before calling an LLM. Unrelated chat continues through the existing memory and model path.
2. A small tool runtime allows only registered tools, validates the zero-argument status call, enforces one call per assistant request, and records only tool name, elapsed time, and outcome. It never logs credentials, shelf payloads, or user text.
3. `HubStatusTool` calls `StatusShelfReader`, which sends `GET /api/status/shelf` with the dedicated `X-Status-Read-Credential` header and a bounded timeout. The credential comes from `ASCEND_STATUS_READ_CREDENTIAL`; the URL follows Vision's Core URL configuration. Existing Vision, producer, and integration credentials are not substitutes.
4. The reader validates schema version, top-level fields, timestamps, service identity/state/heartbeat fields, and response size. It normalizes the shelf into a small typed snapshot. Core's `generatedAt` and each heartbeat are checked for freshness. A malformed, unauthorized, unreachable, or stale shelf yields a deterministic cannot-verify answer.
5. The tool renders individual or all-agent answers. `working`, `idle`, `offline`, and `stuck` map to direct status language. `stuck` is explained as blocked. Multiple instances remain distinct. An idle agent is never described as finished.

## Query behavior

Recognize questions about Ascend Hub, AI/agent status, Antigravity, Codex CLI, Core, or Vision when they ask about status, activity, availability, or completion. Match requested names against normalized service and instance IDs from the shelf. General questions list agent, assistant, bot, and Vision instances; provider-only rows are omitted unless named. Cap the response to a concise set and say when more instances exist. An unknown named instance gets an explicit not-found answer, without falling through to an LLM guess.

## Security and data lifetime

The Core shelf is the sole authority for current status. Status labels, activity labels, and issue messages are untrusted input; rendering uses only validated states and bounded safe display labels. Raw shelf data is not stored in memory, SQLite, or logs. No credential is included in model context or dashboard responses. The reader fails closed if the dedicated credential is absent. No user confirmation is needed for this read-only call.

## Verification

- Reader tests cover request method/path/header, timeout, unauthorized/network errors, schema validation, and stale/contradictory data.
- Tool/runtime tests cover one-call read-only policy, status mapping, target selection, multiple instances, unknown agents, and no `finished` claim without completion evidence.
- Assistant and startup integration tests prove voice/dashboard share the tool, status questions bypass the model, unrelated chat does not, missing credentials fail closed, and Vision stays operational if the status reader fails.
- Full Vision tests and JavaScript syntax checks remain green.
