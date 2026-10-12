# Vision web use (personal, laptop first) - plan

Status: W0 and W1 built and tested on 2026-10-12 (branch `feature/simple-focus-ui`); W2-W5 not started. Live Gemini runs are waiting for the owner at the laptop.

## Decisions from the owner

- Audience: just the owner. Phone start/approve comes later, after laptop features work.
- Approvals: Vision may read on its own once a task is started (open public pages, follow links, scroll, read). Every click that is not a link, every typed value, selection, upload and download shows an exact card and waits for a yes.
- First tasks: look things up, fill forms and click, signed-in sites, and a separate distraction-watch feature.
- The browser is Google Chrome. Vision launches the installed Chrome as a separate, visible window with its own empty profile (owner left the choice to us; separate window chosen so a web page can never reach everyday logins). It never reads the everyday Chrome profile, and the owner can keep using any other app meanwhile.
- Start and approve tasks inside the Vision window (new Browser tab), plus the existing "use the browser to..." chat/voice phrase. Chrome itself is a normal separate window.
- Following a link counts as reading (no card). Changeable later.
- Spending: no price-table or dollar gate. The owner keeps several Gemini and Groq keys that the router already rotates; add a reminder when the keys are close to running out (see W2).
- Distraction watch: match the active Chrome window title against an editable site list, keep only yes/no, discard the title.

## What already exists (do not rebuild)

`browser/` holds a finished-in-code, switched-off system: Playwright executor, task service, planner (Gemini, JSON only), policy (public research and selected-origin scopes), review cards, DPAPI-protected saved profiles, upload/download limits, action journal, budgets and metrics, two documentation routines. Roughly 6,400 lines with tests. The gaps are not engineering: live model acceptance, a spoken-completion check, and UI in the single window. See `docs/browser-automation.md` and `docs/audits/2026-09-29-browser-b5-pilot.md`.

## Phases

### W0 - Use Chrome and make it installable (DONE 2026-10-12)
- Add `browser_automation.browser: chrome` (fallback `chromium`). In `browser/executor.py` launch with `channel='chrome'` when selected; clear error if Chrome is missing.
- `Setup.bat`: optional question "Let Vision use the web? (y/N)". Yes installs `requirements-browser.txt`, skips the Chromium download when Chrome is found, and writes `browser_automation.enabled: true`.
- Exit: fixture suite passes on Chrome; no `playwright install` needed on a machine with Chrome.

### W1 - Browser tab in the Vision window (DONE 2026-10-12)
- Register the existing `/api/browser/*` routes on the Fairy server (`focus_ui.py`), reusing `browser_task_client`, which lives in the same process. Keep the Fairy origin and size guards.
- New tab: goal box, live step list, one approval card at a time (exact site, target, value, Approve/Reject), Stop, final answer with source links. Voice completion notice stays.
- Remove duplicated dashboard browser code only after this tab covers it.
- Exit: Playwright UI tests for start, approve, reject, stop, and failure states; a failed provider never shows as success.

### W2 - Look things up (public, read-only)
- This is B2. Run the five supervised live Gemini tasks and confirm spoken completion on the laptop. Record real token and latency numbers.
- Remove the need for the 7-day pilot and price-table gates for personal use (`pilot_*` config, `browser/budgets.py`).
- Key-low reminder: the router already rotates comma-separated keys and puts a limited key on cooldown. Add a counter of keys currently in cooldown per provider; when all but one (or all) are limited, show an "AI keys running low" badge in the Vision window and say it once. There is no API that reports remaining quota, so this reacts to real rate-limit errors rather than predicting. Also allow the browser provider to be Groq or Gemini and let the planner use the whole key pool (currently capped at two attempts).
- Exit: five accepted tasks with source-linked answers; prompt-injection fixtures still fail closed.

### W3 - Fill forms and click
- This is B3 selected-origin tasks. The owner names one exact site and the allowed actions per task; every fill, select, click, upload and download uses a card.
- Never allowed: payments, security or account changes, arbitrary file paths, redirects off the selected site.
- Exit: fixture forms pass; two real low-risk forms (for example a newsletter signup you choose) completed with cards and verified by you.

### W4 - Signed-in sites
- Vision opens its own Chrome window; you sign in yourself; passwords and MFA never reach the planner. Profile saving is a separate explicit action (DPAPI, current Windows user).
- Start with one site you choose. Do not enable on a shared Windows account.
- Exit: logout, expired session, and "profile cleared" paths behave and are shown honestly.

### W5 - Distraction watch (separate feature, not browsing)
- Today Vision only knows an app category and discards window titles. Site-level awareness needs a new signal.
- Recommended: opt-in, match the active browser window title locally against a list you edit (for example YouTube, Instagram), keep only "distracting: yes/no" in memory, discard the title. Nudge through the existing proactive rules (cooldowns, daily limits, pause). No history, no blocking.
- Alternative: a small Chrome extension (exact URL, but more to install and maintain). Decide before building.
- Exit: three recorded shadow sessions reviewed by you before any spoken nudge (same rule as the proactive companion).

### Later - phone
- Start and approve from the phone through the existing B4 path after W1-W4 are accepted on the laptop and the Core phone setup is finished.

## Safety rules that do not change

- Page text and labels are untrusted data; they cannot add actions, sites, providers or budgets.
- Stop revokes future actions; it cannot undo something a site already did. Say so in the UI.
- The journal records that an action was dispatched, not that a site saved it; the UI says "check the site", never "done" without evidence.
- No automatic provider fallback for browser data. Secrets are never typed by Vision.
- The feature stays off by default until W2 acceptance passes.

## Order and size

W0 and W1 first (about one focused session each), then W2 (needs your Gemini key and about an hour at the laptop). W3-W5 each follow only after the previous exit passes.

## Built so far (W0 + W1)

- `browser_automation.browser` (`chrome` default, `chromium` fallback) and `BrowserExecutor(browser=...)`; clear error when Chrome is missing. Real Google Chrome passed the 20 existing executor and acceptance fixture tests headlessly.
- `setup_wizard.py` asks "Turn on web use?", checks for Chrome, installs `requirements-browser.txt`, and switches on only the `browser_automation` block.
- The Vision server now serves `/api/browser/*` (session `local-fairy`), reports `webUse` in its state, and the window shows a Browser tab only when web use is on.

## Still to do before relying on it

- W2 live acceptance (five real Gemini tasks and a spoken completion check) with the owner present.
- Signed-in and form tasks (B3, `b3_enabled`) are off and have no UI entry yet (W3/W4).
