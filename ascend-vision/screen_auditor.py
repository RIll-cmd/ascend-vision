"""Ephemeral 30-Minute Screen Auditor with multimodal vision LLM classification.

Security Guarantee:
Desktop screenshots are strictly temporary files (temp_audit_{timestamp}.jpg)
that are ALWAYS deleted in a finally block immediately after inference.
No screenshots persist on disk.
"""
from __future__ import annotations
import base64
import ctypes
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable, Optional, Tuple

from PIL import Image, ImageGrab

LOG = logging.getLogger(__name__)

AUDIT_CATEGORIES = [
    "STUDYING_CODING",
    "WATCHING_STREAM_OR_VIDEO",
    "GAMING",
    "IDLE_DESKTOP"
]

AUDIT_PROMPT = (
    "Analyze this user desktop screenshot. Classify the user's primary activity into one category: "
    "[STUDYING_CODING, WATCHING_STREAM_OR_VIDEO, GAMING, IDLE_DESKTOP]. "
    "Return the classification followed by a 1-sentence observation in format: "
    "CATEGORY: <CATEGORY>\nOBSERVATION: <Observation>"
)


def capture_desktop() -> Image.Image:
    """Captures the current desktop on Windows with session attach safety."""
    # Ensure Windows thread is attached to the active input desktop
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception as exc:
        LOG.debug("Desktop handle attachment notice: %s", exc)

    try:
        return ImageGrab.grab(all_screens=True)
    except Exception:
        return ImageGrab.grab()


class MultimodalScreenClassifier:
    """Performs multimodal vision inference using Gemini or Groq vision, with heuristic fallback."""

    def __init__(self, model_name: str = "gemini-3.6-flash"):
        self.model_name = model_name

    def classify(self, image_path: str) -> Tuple[str, str]:
        """Classifies desktop screenshot.

        Returns:
            Tuple[str, str]: (category, observation)
        """
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()

        # 1. Try Gemini Multimodal
        if gemini_key:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=gemini_key)
                img = Image.open(image_path)
                # Downscale for fast API transmission if high-res
                if max(img.size) > 1280:
                    img.thumbnail((1280, 1280))
                config = types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    max_output_tokens=250,
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_level="minimal") if "flash" in self.model_name else None
                )
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=[img, AUDIT_PROMPT],
                    config=config
                )
                text = response.text if response and response.text else ""
                if text:
                    return self._parse_response(text)
            except Exception as err:
                LOG.warning("Gemini multimodal screen analysis failed: %s", err)

        # 2. Try Groq Vision
        if groq_key:
            try:
                from groq import Groq
                client = Groq(api_key=groq_key)
                with open(image_path, "rb") as f:
                    b64_img = base64.b64encode(f.read()).decode("utf-8")

                completion = client.chat.completions.create(
                    model="llama-3.2-90b-vision-preview",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": AUDIT_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                                }
                            ]
                        }
                    ],
                    max_tokens=100
                )
                text = completion.choices[0].message.content or ""
                if text:
                    return self._parse_response(text)
            except Exception as err:
                LOG.warning("Groq vision analysis failed: %s", err)

        # 3. Fallback Heuristic
        return self._heuristic_fallback()

    def _parse_response(self, text: str) -> Tuple[str, str]:
        upper_text = text.upper()
        detected_category = "IDLE_DESKTOP"
        for cat in AUDIT_CATEGORIES:
            if cat in upper_text:
                detected_category = cat
                break

        # Extract observation
        obs_match = re.search(r"OBSERVATION:\s*(.+)", text, re.IGNORECASE)
        if obs_match:
            observation = obs_match.group(1).strip()
        else:
            candidates = [
                l.strip() for l in text.splitlines()
                if l.strip() and not l.strip().upper().startswith("CATEGORY") and len(l.strip()) > 3
            ]
            observation = candidates[0] if candidates else "Desktop activity analyzed."

        return detected_category, observation

    def _heuristic_fallback(self) -> Tuple[str, str]:
        """Inspects open windows when vision API is unavailable."""
        try:
            import win32gui
            titles = []

            def enum_w(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    t = win32gui.GetWindowText(hwnd).lower()
                    if t:
                        titles.append(t)

            win32gui.EnumWindows(enum_w, None)
            all_text = " ".join(titles)

            if any(k in all_text for k in ["code", "pycharm", "cursor", "visual studio", "terminal", "powershell", "cmd"]):
                return "STUDYING_CODING", "Code editor and workspace active on screen."
            if any(k in all_text for k in ["youtube", "netflix", "twitch", "stream", "video", "bilibili"]):
                return "WATCHING_STREAM_OR_VIDEO", "Video streaming detected in browser window."
            if any(k in all_text for k in ["steam", "epic games", "valorant", "league", "genshin", "minecraft"]):
                return "GAMING", "Game client running in foreground."
        except Exception as exc:
            LOG.debug("Heuristic window scan failed: %s", exc)

        return "IDLE_DESKTOP", "System idle or desktop background active."


class ScreenAuditor:
    """Manages the 30-minute desktop auditing loop and ephemeral image cleanup."""

    def __init__(
        self,
        feedback_service=None,
        interval_seconds: float = 1800.0,
        classifier: Optional[MultimodalScreenClassifier] = None,
        on_audit_complete: Optional[Callable[[str, str], None]] = None
    ):
        self.feedback_service = feedback_service
        self.interval_seconds = float(interval_seconds)
        self.classifier = classifier or MultimodalScreenClassifier()
        self.on_audit_complete = on_audit_complete
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def audit_once(self) -> Tuple[str, str]:
        """Captures desktop, runs vision inference, guarantees immediate deletion, and routes feedback."""
        timestamp = int(time.time())
        temp_path = f"temp_audit_{timestamp}.jpg"

        try:
            screenshot = capture_desktop()
            # Convert RGBA to RGB for JPEG compatibility
            if screenshot.mode in ("RGBA", "P"):
                screenshot = screenshot.convert("RGB")
            screenshot.save(temp_path, format="JPEG", quality=85)
            LOG.info("Ephemeral desktop screenshot saved: %s", temp_path)

            category, observation = self.classifier.classify(temp_path)
            LOG.info("SCREEN_AUDIT: category=%s | observation='%s'", category, observation)
        finally:
            # Mandatory immediate deletion: never leave screenshots on disk
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    LOG.debug("Ephemeral screenshot deleted immediately: %s", temp_path)
                except Exception as del_err:
                    LOG.warning("Failed to remove temp screenshot %s: %s", temp_path, del_err)

        if self.on_audit_complete:
            try:
                self.on_audit_complete(category, observation)
            except Exception as cb_err:
                LOG.debug("on_audit_complete callback failed: %s", cb_err)

        # Route to TTS via feedback service
        self._route_feedback(category)
        return category, observation

    def _route_feedback(self, category: str):
        if category == "IDLE_DESKTOP":
            LOG.debug("Screen audit was IDLE_DESKTOP; skipping voice feedback.")
            return

        event_map = {
            "STUDYING_CODING": "screen_studying_coding",
            "WATCHING_STREAM_OR_VIDEO": "screen_watching_video",
            "GAMING": "screen_gaming",
        }
        event_name = event_map.get(category)
        if event_name and self.feedback_service is not None:
            submit_expr = getattr(self.feedback_service, "submit_expression", None)
            if submit_expr is not None:
                submit_expr(event_name)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ScreenAuditorThread", daemon=True)
        self._thread.start()
        LOG.info("ScreenAuditor loop started (interval=%.0fs)", self.interval_seconds)

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        LOG.info("ScreenAuditor loop stopped.")

    def _run_loop(self):
        while not self._stop_event.is_set():
            # Wait for interval or stop signal
            if self._stop_event.wait(timeout=self.interval_seconds):
                break
            try:
                self.audit_once()
            except Exception as err:
                LOG.error("Screen audit cycle encountered error: %s", err)
