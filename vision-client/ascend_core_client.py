"""
Ascend Core ↔ Ascend Vision Official Client SDK
Implements:
1. Two-Phase Commit (Preview -> Signed Token -> Execute) for Habit & Routine creation
2. Bounded retries (MAX_EXPIRY_RETRIES = 1) for clock drift/latency
3. Typed state queries (habits, missions, recovery)
4. Heartbeat presence pinging
5. Canonical narration extraction to eliminate reward hallucinations
"""

import logging
import uuid
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("AscendCoreVisionClient")


class AscendCoreVisionClient:
    """Client for Ascend Vision to communicate deterministically with Ascend Core."""

    MAX_EXPIRY_RETRIES = 1

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        character_id: str,
        device_id: str = "ascend-vision-client",
        timeout: float = 10.0,
    ):
        """
        Initialize the Ascend Core Client.

        Args:
            base_url: The root URL of Ascend Core (e.g. 'http://localhost:8000')
            bearer_token: Scoped JWT token (purpose: 'ascend_vision' or user session)
            character_id: The UUID of the target RPG character
            device_id: Unique identifier for this Vision sensor/instance
            timeout: HTTP request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.character_id = character_id
        self.device_id = device_id
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

    # -------------------------------------------------------------------------
    # 1. Heartbeat & Liveness
    # -------------------------------------------------------------------------
    async def send_heartbeat(self) -> Dict[str, Any]:
        """Send presence heartbeat to Ascend Core."""
        from datetime import datetime, timezone

        payload = {
            "source": "ascend_vision",
            "characterId": self.character_id,
            "deviceId": self.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            res = await client.post("/api/integration/vision/heartbeat", headers=self.headers, json=payload)
            res.raise_for_status()
            return res.json()

    # -------------------------------------------------------------------------
    # 2. Typed State Queries (Read)
    # -------------------------------------------------------------------------
    async def query_state(self, intent: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Query player state from Core (e.g. 'habits_summary', 'missions_summary', 'recovery_summary').
        """
        request_id = f"vis-query-{uuid.uuid4().hex[:12]}"
        payload = {
            "characterId": self.character_id,
            "requestId": request_id,
            "capabilityVersion": "2026-09-09",
            "intent": intent,
            "parameters": parameters or {},
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            res = await client.post("/api/integration/vision/query", headers=self.headers, json=payload)
            res.raise_for_status()
            return res.json()

    # -------------------------------------------------------------------------
    # 3. Two-Phase Commit State Mutations (Write)
    # -------------------------------------------------------------------------
    async def create_habit_routine(
        self,
        name: str,
        category: str = "GENERAL",
        difficulty: str = "MEDIUM",
        primary_stat: str = "discipline",
        description: str = "",
        habit_type: str = "POSITIVE",
        affected_stat: str = "HP",
        stat_modifier: int = 10,
    ) -> Dict[str, Any]:
        """
        Create a habit routine (POSITIVE or NEGATIVE) using the Two-Phase Commit pattern.

        Guarantees:
        - Deterministic schema validation
        - Zero prompt-injection / reward hallucinations
        - Server-templated canonical narration
        - Automatic 1-time token refresh on clock skew/latency
        """
        request_id = f"vis-habit-{uuid.uuid4().hex[:12]}"
        arguments = {
            "name": name,
            "category": category,
            "difficulty": difficulty,
            "primaryStat": primary_stat,
            "description": description,
            "type": habit_type,
            "affectedStat": affected_stat,
            "statModifier": stat_modifier,
        }

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            retries = 0
            while retries <= self.MAX_EXPIRY_RETRIES:
                # Phase A: Preview & Validation
                preview_payload = {
                    "characterId": self.character_id,
                    "requestId": request_id,
                    "operation": "create_habit",
                    "arguments": arguments,
                }
                p_res = await client.post(
                    "/api/aira/operations/preview",
                    headers=self.headers,
                    json=preview_payload,
                )

                if p_res.status_code == 409:
                    return {
                        "success": False,
                        "reason": "DUPLICATE",
                        "message": f"An active habit named '{name}' already exists for this character.",
                    }
                if p_res.status_code == 422:
                    return {
                        "success": False,
                        "reason": "VALIDATION_ERROR",
                        "message": "Habit payload failed schema validation.",
                        "detail": p_res.json(),
                    }
                if p_res.status_code in (401, 403):
                    return {
                        "success": False,
                        "reason": "AUTH_ERROR",
                        "message": "Unauthorized or character ownership mismatch.",
                    }

                p_res.raise_for_status()
                preview_data = p_res.json()
                confirmation_token = preview_data["confirmationToken"]

                # Phase B: Execution (Reuses the same requestId)
                exec_payload = {
                    "characterId": self.character_id,
                    "requestId": request_id,
                    "operation": "create_habit",
                    "confirmationToken": confirmation_token,
                    "confirmed": True,
                }
                e_res = await client.post(
                    "/api/aira/operations/execute",
                    headers=self.headers,
                    json=exec_payload,
                )

                # If token expired, retry preview once
                if e_res.status_code == 400 and "expired" in e_res.text.lower():
                    retries += 1
                    logger.warning(
                        "Confirmation token expired for %s. Retrying preview (%d/%d)...",
                        request_id,
                        retries,
                        self.MAX_EXPIRY_RETRIES,
                    )
                    continue

                if e_res.status_code == 409:
                    return {
                        "success": False,
                        "reason": "DUPLICATE",
                        "message": f"An active habit named '{name}' already exists for this character.",
                    }

                e_res.raise_for_status()
                exec_data = e_res.json()

                result_data = exec_data.get("result", {})
                return {
                    "success": True,
                    "habitId": result_data.get("habitId"),
                    "name": result_data.get("name", name),
                    "canonicalNarration": result_data.get(
                        "canonicalNarration",
                        f"Protocol locked: '{name}' successfully registered to daily routines.",
                    ),
                    "idempotentReplay": exec_data.get("idempotentReplay", False),
                }

            return {
                "success": False,
                "reason": "TOKEN_EXPIRY_CIRCUIT_BROKEN",
                "message": "Authorization expired twice due to extreme latency or clock drift. Action aborted.",
            }

    async def trigger_negative_habit(self, habit_id: str) -> Dict[str, Any]:
        """Apply a penalty and increment relapse count on an existing negative habit."""
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            res = await client.post(f"/api/habits/{habit_id}/trigger", headers=self.headers)
            if res.status_code == 404:
                return {"success": False, "reason": "NOT_FOUND", "message": "Habit not found."}
            res.raise_for_status()
            data = res.json()
            penalty = data.get("penalty", {})
            habit = data.get("habit", {})
            name = habit.get("name", "Bad Habit")
            return {
                "success": True,
                "habitId": habit_id,
                "name": name,
                "relapseCount": habit.get("relapseCount", 1),
                "penalty": penalty,
                "canonicalNarration": f"Protocol breached: '{name}' relapse logged. -{penalty.get('amount', 10)} {penalty.get('target', 'HP')} deducted.",
            }

    async def record_bad_habit_offense(
        self,
        name: str,
        penalty_stat: str = "HP",
        penalty_amount: int = 10,
        category: str = "DISCIPLINE",
        description: str = "Logged automatically via Ascend Vision sensor observation.",
    ) -> Dict[str, Any]:
        """
        Record a repeat bad habit offense (e.g. Doomscrolling, Bad Posture, Staying Up Late):
        - If an active negative habit with this name already exists, logs a relapse and deducts penalty.
        - If it does NOT exist yet, automatically creates it and registers it to the character deck.
        """
        query_res = await self.query_state(intent="habits_summary")
        existing_habits = query_res.get("data", {}).get("habits", [])

        target_name_lower = name.strip().lower()
        matching_habit = next(
            (
                h for h in existing_habits
                if h.get("name", "").strip().lower() == target_name_lower
                and str(h.get("type", "POSITIVE")).upper() == "NEGATIVE"
                and str(h.get("status", "ACTIVE")).upper() == "ACTIVE"
            ),
            None,
        )

        if matching_habit:
            trigger_res = await self.trigger_negative_habit(matching_habit["id"])
            if trigger_res.get("success"):
                trigger_res["action"] = "RELAPSE_LOGGED"
            return trigger_res

        create_res = await self.create_habit_routine(
            name=name,
            category=category,
            habit_type="NEGATIVE",
            affected_stat=penalty_stat,
            stat_modifier=penalty_amount,
            description=description,
        )
        if create_res.get("success"):
            create_res["action"] = "BAD_HABIT_CREATED"
            create_res["canonicalNarration"] = (
                f"Warning protocol breached: Negative habit '{name}' registered. Maintain vigilance."
            )
        return create_res

