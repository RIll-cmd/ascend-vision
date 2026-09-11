# Ascend Vision ↔ Ascend Core Client SDK

Production-grade integration package for **Ascend Vision** to communicate with **Ascend Core**.

---

## 📦 Requirements

* **Python:** 3.10+
* **Dependencies:**
  ```bash
  pip install httpx
  ```

---

## 🚀 Quickstart

```python
import asyncio
from ascend_core_client import AscendCoreVisionClient

client = AscendCoreVisionClient(
    base_url="http://localhost:8000",
    bearer_token="<your-vision-or-user-jwt>",
    character_id="<your-character-uuid>",
    device_id="ascend-vision-desktop",
)

async def main():
    # 1. Send presence heartbeat
    ping = await client.send_heartbeat()
    print("Heartbeat:", ping)

    # 2. Query player's current habits
    habits = await client.query_state(intent="habits_summary")
    print("Active Habits:", habits)

    # 3. Create a new habit routine & mission via Two-Phase Commit
    result = await client.create_habit_routine(
        name="30min Python Practice",
        category="KNOWLEDGE",
        difficulty="MEDIUM",
        primary_stat="knowledge",
        description="Daily deep work block.",
    )
    print("Creation Result:", result)

    # Vision speaks the canonical narration directly to the user
    if result["success"]:
        print("Voice Output:", result["canonicalNarration"])

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🛡️ Integration Checkpoints

1. **Heartbeat:** `POST /api/integration/vision/heartbeat` returns `{"status": "CONNECTED"}`.
2. **Read Intent:** `POST /api/integration/vision/query` with `intent: "habits_summary"` returns active habit records.
3. **Two-Phase Write:** `POST /api/aira/operations/preview` generates token $\to$ `POST /api/aira/operations/execute` commits to database.
4. **Idempotency Proof:** Re-executing with identical `requestId` returns `idempotentReplay: true` without duplicate record creation.
