# Vision Browser Automation B1/B2 Local Acceptance

**Date:** 2026-09-28
**Scope:** B1 controller and B2 supervised public research in the existing local Vision worktree.
**Result:** Local implementation and deterministic acceptance pass. B2 is not fully accepted because live Gemini evaluation and real voice-output confirmation require owner-provisioned credentials and device validation.

## B1 — Controller and safety boundary

The B1 controller is implemented with a separate Playwright broker, owner-authenticated Windows named-pipe IPC, a local authenticated dashboard surface, bounded task/event handling, pause/stop controls, source observation references, and public-research network/action policy. Forms and authenticated workflows remain unavailable. Browser automation remains disabled by default.

Terminal results and page/event content are removed after one hour of terminal inactivity. A content-free tombstone preserves only the task ID, complete owner/channel/session tuple, terminal state, final cursor, and erasure time for at most 24 hours. Retention uses a monotonic clock. The registry is capped at 256 total active, queued, terminal, and tombstone entries; excess submissions fail without evicting existing tasks. Shutdown therefore scans a bounded registry.

Evidence:

- Existing browser IPC tests cover a separate broker process, invalid credentials, request-size bounds, session ownership, and credential cleanup.
- `python -m pytest tests/test_browser_service.py -q` — **22 passed**.
- Review of retention/capacity commit `c4dab3b5e894a2d0aebdeb39b0ec0c84d1f630ed` found no remaining critical or important issues.
- Plan-marked secondary-Windows-account ACL verification remains an external release check. The current tests establish same-user IPC authentication and the owner-restricted pipe configuration, not a connection attempt from a second Windows account.

## B2 — Deterministic end-to-end fixture acceptance

Added `tests/test_browser_acceptance.py`, which runs the actual Playwright executor and task service against a local HTTP fixture site using a deterministic scripted decision provider.

| Scenario | Result |
|---|---:|
| Five fixture research goals, repeated twice | 10/10 completed |
| Verified, source-linked findings | 10/10 |
| Browser navigation dispatches | 10 |
| Prompt-injection fixtures | 5/5 blocked before any unauthorized follow-up request |
| Fixture requests from injection cases | One initial fixture navigation each; no form submission or extra navigation |
| Model tokens | N/A — scripted provider, no LLM request |
| Dedicated fixture-suite elapsed time | 12.83 seconds on the recorded run |

The fixtures exercise hostile page text that asks Vision to click a button, fill a field, change a selection, access a private metadata address, and use a JavaScript URL. The service fails closed and does not report findings for those tasks. Existing executor tests additionally cover delayed content, duplicate labels, mutated link targets, popup ownership, denied forms, private redirects, and WebSocket blocking.

Commands and results:

```text
python -m pytest tests/test_browser_acceptance.py -q
6 passed in 12.83s

python -m pytest -q
892 passed, 1 warning in 64.27s
```

The single full-suite warning is the installed Google GenAI SDK using a Python 3.14 deprecated typing alias; it is unrelated to browser automation.

## Still required before B2 is accepted

- The selected provider is Gemini, but no `GEMINI_API_KEY` is provisioned in this environment. The real-model acceptance requirement—five public documentation-site tasks with actual provider calls, success/action/latency/token/failure metrics—has not been performed. Configure the key locally (do not paste it into chat), then run supervised acceptance and record the provider's actual token/latency metadata.
- The five public documentation pages previously opened with Chromium were a browser-runtime smoke test only; they did not use the model and are not counted as B2 model-task successes.
- The voice completion notifier has automated tests, but a human must confirm the spoken completion on the target laptop.
- Keep `browser_automation.enabled: false` until those checks are completed and reviewed.

## Scope exclusions

This closes no B3+ behavior. There is no saved login profile, arbitrary form editing/submission, upload/download support, phone/PWA/Discord initiation, browser memory persistence, or OS-level network sandbox claim. HTTP redirects are refused and WebSockets are blocked; origin policy remains defense in depth rather than a complete DNS-rebinding or browser-network boundary.
