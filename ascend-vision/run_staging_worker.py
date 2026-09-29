import os
import time
from dotenv import load_dotenv

load_dotenv('.env')
from config import load_config
from assistant.service import AssistantService
from assistant.memory import UnavailableMemoryStore
from assistant.phone_handler import PhoneMessageHandler
from integrations.phone_worker import build_phone_worker

print("[Worker] Loading config and starting phone worker against staging...")
cfg = load_config('config.yaml')
service = AssistantService(cfg.feedback, cfg.llm, memory_store=UnavailableMemoryStore())
phone_owner_id = os.getenv('ASCEND_PHONE_OWNER_ID')
phone_handler = PhoneMessageHandler(service, owner_id=phone_owner_id)
worker = build_phone_worker(cfg.phone_chat, phone_handler)
if worker is None:
    raise RuntimeError("Failed to build worker")
worker.start()
print("[Worker] Running and polling staging Core queue at:", os.getenv("ASCEND_PHONE_CORE_URL"))
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("[Worker] Stopping worker...")
    worker.stop()
