"""Supervised B2 live acceptance runner for real Gemini model evaluation and voice verification."""

import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from browser.contracts import BrowserTaskRequest
from browser.executor import BrowserExecutor
from browser.planner import BrowserPlanner
from browser.policy import BrowserPolicy
from browser.service import BrowserTaskService
from config import load_config
from integrations.browser_voice_notifier import BrowserVoiceCompletionNotifier
from llm_router import LLMRouter
from speech import OfflineSpeaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("b2_live_acceptance")

RESEARCH_TASKS = [
    {
        "id": "b2-python-docs",
        "name": "Python Documentation",
        "url": "https://docs.python.org/3/",
        "goal": "Navigate to https://docs.python.org/3/ and find how Python describes itself or its official release highlights.",
    },
    {
        "id": "b2-playwright-intro",
        "name": "Playwright Documentation",
        "url": "https://playwright.dev/python/docs/intro",
        "goal": "Navigate to https://playwright.dev/python/docs/intro and find what browser automation capabilities Playwright provides.",
    },
    {
        "id": "b2-sqlite-about",
        "name": "SQLite Documentation",
        "url": "https://www.sqlite.org/about.html",
        "goal": "Navigate to https://www.sqlite.org/about.html and find how SQLite describes its database engine characteristics.",
    },
    {
        "id": "b2-mdn-http",
        "name": "MDN HTTP Overview",
        "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview",
        "goal": "Navigate to https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview and find the definition of HTTP as an application layer protocol.",
    },
    {
        "id": "b2-pytest-docs",
        "name": "Pytest Documentation",
        "url": "https://docs.pytest.org/en/stable/",
        "goal": "Navigate to https://docs.pytest.org/en/stable/ and find what kind of software tests pytest makes easy to write.",
    },
]


def check_gemini_key_available() -> bool:
    """Checks whether GEMINI_API_KEY is available in os.environ or Windows User registry."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return True
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rkey:
                val, _ = winreg.QueryValueEx(rkey, "GEMINI_API_KEY")
                if (val or "").strip():
                    os.environ["GEMINI_API_KEY"] = val.strip()
                    return True
        except Exception:
            pass
    return False


def wait_for_terminal_page(service, task_id: str, timeout_seconds: float = 120.0):
    session_key = ("acceptance-owner", "dashboard", "b2-live-session")
    deadline = time.monotonic() + timeout_seconds
    cursor = 0
    all_events = []
    while time.monotonic() < deadline:
        page = service.events(task_id, cursor, session_key)
        cursor = page.next_cursor
        all_events.extend(page.events)
        if page.state in {"completed", "failed", "partial", "cancelled", "unknown"}:
            return page, all_events
        time.sleep(0.5)
    raise TimeoutError(f"Task {task_id} timed out after {timeout_seconds} seconds")


def run_live_b2_acceptance(voice_task_index: int = 2):
    if not check_gemini_key_available():
        LOG.error("GEMINI_API_KEY is not provisioned in the process environment or Windows User registry.")
        print("\n[ERROR] GEMINI_API_KEY is missing.")
        print("To provision it securely without showing in logs, command args, or git:")
        print("Run the following in your local PowerShell:")
        print("  [Environment]::SetEnvironmentVariable('GEMINI_API_KEY', (Read-Host -Prompt 'Enter Gemini Key' -AsSecureString | ForEach-Object { [System.Net.NetworkCredential]::new('', $_).Password }), 'User')\n")
        return None

    cfg = load_config(REPO_ROOT / "config.yaml")
    router = LLMRouter(cfg.llm)
    planner = BrowserPlanner(router)

    task_telemetry = {}

    def instrumented_decide(request, observation):
        t_start = time.monotonic()
        decision = planner.propose(request, observation)
        elapsed_ms = round((time.monotonic() - t_start) * 1000)

        tid = request.task_id
        if tid not in task_telemetry:
            task_telemetry[tid] = {
                "decisions": [],
                "actions": [],
                "total_llm_latency_ms": 0,
            }

        last_comp = getattr(planner, "last_completion", None)
        in_tok = getattr(last_comp, "input_tokens", None) or 0
        out_tok = getattr(last_comp, "output_tokens", None) or 0

        rec = {
            "action": decision.action,
            "latency_ms": elapsed_ms,
            "observation_id": decision.observation_id,
            "args": decision.arguments,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }
        task_telemetry[tid]["decisions"].append(rec)
        task_telemetry[tid]["actions"].append(decision.action)
        task_telemetry[tid]["total_llm_latency_ms"] += elapsed_ms
        task_telemetry[tid]["total_input_tokens"] = task_telemetry[tid].get("total_input_tokens", 0) + in_tok
        task_telemetry[tid]["total_output_tokens"] = task_telemetry[tid].get("total_output_tokens", 0) + out_tok
        return decision

    policy = BrowserPolicy()
    service = BrowserTaskService(
        executor_factory=lambda: BrowserExecutor(policy=policy, headless=False, max_pages=3),
        decision_provider=instrumented_decide,
        max_decisions=15,
        max_actions=20,
        max_pages=3,
        max_queued_tasks=4,
        task_timeout_seconds=120,
    )

    results_summary = []
    service.start()
    LOG.info("BrowserTaskService started with real Gemini model: %s", cfg.llm.gemini_model)

    try:
        for idx, task_info in enumerate(RESEARCH_TASKS):
            tid = task_info["id"]
            goal = task_info["goal"]
            LOG.info("=== Running Task %d/5: %s (%s) ===", idx + 1, task_info["name"], tid)

            req = BrowserTaskRequest(
                task_id=tid,
                goal=goal,
                channel="dashboard",
                session_id="b2-live-session",
                owner="acceptance-owner",
                provider="gemini",
                timeout_seconds=120,
            )

            t0 = time.monotonic()
            service.submit(req)
            page, events = wait_for_terminal_page(service, tid, timeout_seconds=120.0)
            elapsed_sec = round(time.monotonic() - t0, 2)

            telemetry = task_telemetry.get(tid, {})
            result_obj = page.result
            sources_cited = getattr(result_obj, "source_observation_ids", []) if result_obj else []
            finding = getattr(result_obj, "finding", "") if result_obj else ""

            unauthorized_actions = [a for a in telemetry.get("actions", []) if a not in {
                "navigate", "observe", "click", "scroll", "back", "wait_for", "ask_user", "finish"
            }]
            no_evidence = (page.state == "completed" and (not sources_cited or not finding))
            has_error = bool(page.error) or bool(unauthorized_actions) or no_evidence

            summary_item = {
                "task_index": idx + 1,
                "task_id": tid,
                "name": task_info["name"],
                "url": task_info["url"],
                "state": page.state,
                "completed": page.state == "completed" and not has_error,
                "action_count": len(telemetry.get("actions", [])),
                "actions": telemetry.get("actions", []),
                "elapsed_seconds": elapsed_sec,
                "llm_latency_ms": telemetry.get("total_llm_latency_ms", 0),
                "input_tokens": telemetry.get("total_input_tokens", 0),
                "output_tokens": telemetry.get("total_output_tokens", 0),
                "sources_cited": len(sources_cited),
                "finding": finding,
                "unauthorized_actions": unauthorized_actions,
                "error": page.error or ("No evidence/sources" if no_evidence else None),
            }
            results_summary.append(summary_item)
            LOG.info(
                "Task %d result: state=%s, actions=%d, elapsed=%.2fs, sources=%d, error=%s",
                idx + 1, page.state, len(telemetry.get("actions", [])), elapsed_sec,
                len(sources_cited), summary_item["error"]
            )

            if idx == voice_task_index and page.state == "completed" and result_obj:
                LOG.info("--- Performing Audible Voice Verification for Task %d ---", idx + 1)
                voice_summary = BrowserVoiceCompletionNotifier._summary(result_obj)
                spoken_text = f"Research complete. {voice_summary}"
                LOG.info("Speaking: %s", spoken_text)
                try:
                    speaker = OfflineSpeaker(cfg.feedback)
                    outcome = speaker.speak(spoken_text, cancelled=lambda: False)
                    speaker.close()
                    summary_item["voice_tested"] = True
                    summary_item["voice_outcome"] = {
                        "started": outcome.started,
                        "completed": outcome.completed,
                        "error": outcome.error,
                        "spoken_text": spoken_text,
                    }
                    LOG.info("Audible voice playback outcome: started=%s, completed=%s", outcome.started, outcome.completed)
                except Exception as voice_err:
                    LOG.error("Voice output failed: %s", voice_err)
                    summary_item["voice_tested"] = True
                    summary_item["voice_outcome"] = {"started": False, "completed": False, "error": str(voice_err)}
            else:
                summary_item["voice_tested"] = False

    finally:
        service.close()
        LOG.info("BrowserTaskService closed.")

    return results_summary


if __name__ == "__main__":
    results = run_live_b2_acceptance()
    if results:
        print("\n=== B2 LIVE ACCEPTANCE RESULTS ===")
        print(json.dumps(results, indent=2))
