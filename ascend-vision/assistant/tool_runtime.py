"""Small permission boundary for explicitly registered read-only assistant tools."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
from typing import Callable


LOG = logging.getLogger(__name__)


class ToolPolicyError(ValueError):
    """The requested tool call is not allowed by the local runtime."""


class ToolCallError(RuntimeError):
    """A registered tool failed or returned an invalid result."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: int
    risk: str
    arguments: frozenset[str]
    output_type: type
    max_calls_per_request: int = 1


class ToolRuntime:
    """Owns trusted tool registration; a request owns its own call budget."""

    def __init__(self):
        self._tools: dict[str, tuple[ToolSpec, Callable]] = {}

    def register(self, spec: ToolSpec, invoke: Callable) -> None:
        if (not isinstance(spec, ToolSpec) or not isinstance(spec.name, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]*", spec.name)
                or type(spec.version) is not int or spec.version <= 0
                or spec.risk != "read-only" or spec.arguments != frozenset()
                or type(spec.max_calls_per_request) is not int
                or spec.max_calls_per_request != 1
                or not isinstance(spec.output_type, type) or not callable(invoke)
                or spec.name in self._tools):
            raise ToolPolicyError("Invalid or unsupported assistant tool")
        self._tools[spec.name] = (spec, invoke)

    def start_request(self) -> ToolRequest:
        return ToolRequest(self._tools)


class ToolRequest:
    def __init__(self, tools: dict[str, tuple[ToolSpec, Callable]]):
        self._tools = tools
        self._calls: dict[str, int] = {}

    def call(self, name: str, arguments: dict) -> object:
        if not isinstance(name, str) or name not in self._tools:
            raise ToolPolicyError("Assistant tool is not registered")
        spec, invoke = self._tools[name]
        if type(arguments) is not dict or set(arguments) != spec.arguments:
            raise ToolPolicyError("Assistant tool arguments are invalid")
        if self._calls.get(name, 0) >= spec.max_calls_per_request:
            raise ToolPolicyError("Assistant tool call limit reached")
        self._calls[name] = self._calls.get(name, 0) + 1
        started = time.monotonic()
        try:
            result = invoke()
            if not isinstance(result, spec.output_type):
                raise ToolCallError("Assistant tool returned an invalid result")
        except Exception as exc:
            LOG.warning("Assistant tool %s failed (%s) after %.3fs",
                        name, type(exc).__name__, time.monotonic() - started)
            raise ToolCallError("Assistant tool could not complete") from None
        LOG.info("Assistant tool %s succeeded in %.3fs", name, time.monotonic() - started)
        return result
