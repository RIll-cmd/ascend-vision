"""Explicit recovery chat host; full sensing remains in main.py.

Only the existing shared conversation service runs here. No camera, voice,
browser worker, model warmup, or session/penalty loop is started. This mode is
deliberately separate until the full sensing authority can be extracted safely.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sqlite3
import threading

from assistant.runtime_health import RuntimeHealth
from focus_ui import FocusUI


class DesktopHost:
    def __init__(self, config, *, ui=None, assistant_service=None):
        self.config = config
        self.ui = ui or FocusUI(health=RuntimeHealth())
        self.assistant_service = assistant_service
        self._stopped = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._initializer = None
        self._conversation = None
        self._bridge = None
        self._queue = None

    def start(self, *, open_browser=False):
        if self._initializer is not None:
            raise RuntimeError('DesktopHost is single-use')
        self.ui.publish_recovery_mode()
        url = self.ui.start(open_browser=open_browser)
        self._initializer = threading.Thread(target=self._initialize_chat,
                                              name='recovery-chat-start', daemon=True)
        self._initializer.start()
        return url

    def _initialize_chat(self):
        # Imports and storage opening are off the HTTP and supervisor UI threads.
        # AssistantService defers provider construction until the first request.
        try:
            from assistant.local_conversation import LocalConversation
            from assistant.memory import MemoryStore, UnavailableMemoryStore
            from assistant.service import AssistantService
            from feedback import ConversationContext
            from integrations.chat_ipc import ChatIpcQueue
            from integrations.chat_runtime import ChatRuntimeBridge

            data_dir = Path(self.config.storage.database).parent
            service = self.assistant_service
            if service is None:
                try:
                    memory = MemoryStore(data_dir / 'assistant_memory.db')
                except (OSError, sqlite3.Error, RuntimeError):
                    memory = UnavailableMemoryStore()
                service = AssistantService(self.config.feedback, self.config.llm,
                                           memory_store=memory)
            queue = ChatIpcQueue(data_dir / 'chat_ipc.db')
            conversation = LocalConversation(
                queue, service, lambda text: ConversationContext(user_query=text),
                session_key=('local', 'dashboard', self.ui.health.instance_id),
                max_words=self.config.voice_commands.max_reply_words,
            )
            bridge = ChatRuntimeBridge(queue, lambda _text: None,
                                       message_handler=conversation.handle_message)
            with self._lock:
                if self._stopped.is_set():
                    return
                conversation.start()
                self._queue, self._conversation, self._bridge = queue, conversation, bridge
                self.assistant_service = service
                if callable(getattr(service, 'set_ai_status_publisher', None)):
                    def publish(status):
                        queue.publish_ai_status(status)
                        self.ui.publish_ai_status(status)
                    service.set_ai_status_publisher(publish)
                self.ui.bind_chat(queue, conversation.runtime_session_id)
                bridge.start()
                self.ui.mark_chat_ready()
                self._ready.set()
        except Exception:
            # Do not persist provider errors, prompts, credentials, or raw paths.
            self.ui.health.fail('chat_initialization_failed')
            print('ASCEND_STAGE=chat_initialization_failed', flush=True)

    def close(self):
        self._stopped.set()
        with self._lock:
            conversation, bridge, queue = self._conversation, self._bridge, self._queue
        if conversation is not None:
            conversation.stop_accepting()
        if bridge is not None:
            bridge.stop()
        if conversation is not None:
            # A provider call can still be running on the daemon chat thread.
            # End the queue session without waiting on its per-turn lock.
            queue.end_session(conversation.runtime_session_id)
        self.ui.close()

    def serve(self):
        while not self._stopped.wait(.1):
            if self.ui.shutdown_requested.is_set():
                self._stopped.set()
                break
            # Recovery mode has no focus or hardware authority. Drain legacy
            # controls without opening devices or sending consequential actions.
            for _command in self.ui.drain_commands():
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Vision recovery chat without camera or audio')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)
    host = None
    try:
        print('ASCEND_STAGE=recovery_config', flush=True)
        from assistant.runtime_environment import load_runtime_environment
        from config import load_config
        load_runtime_environment(args.config, args.env_file)
        host = DesktopHost(load_config(args.config))
        signal.signal(signal.SIGINT, lambda *_: host._stopped.set())
        signal.signal(signal.SIGTERM, lambda *_: host._stopped.set())
        host.start(open_browser=not args.no_open)
        host.serve()
        return 0
    except Exception:
        print('ASCEND_STAGE=recovery_start_failed; check config, Python dependencies and Fairy build', flush=True)
        return 1
    finally:
        if host is not None:
            host.close()


if __name__ == '__main__':
    raise SystemExit(main())
