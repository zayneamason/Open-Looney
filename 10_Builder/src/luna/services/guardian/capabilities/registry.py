"""
Guardian capability registry — the in-process A2A dispatcher.

One module-level singleton. Handlers are async callables that take
(inputs, engine) and return a CapabilityResult. Registration enforces
read_only=True so Guardian can never silently gain write capabilities.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from .spec import CapabilityResult, CapabilitySpec

logger = logging.getLogger(__name__)

CapabilityHandler = Callable[[dict, Any], Awaitable[CapabilityResult]]


class CapabilityError(Exception):
    """Raised for registration or invocation failures."""


class CapabilityNotFound(CapabilityError):
    """Raised when an unknown capability name is invoked."""


class CapabilityRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        self._handlers: dict[str, CapabilityHandler] = {}
        self._engine: Any = None

    def set_engine(self, engine: Any) -> None:
        self._engine = engine

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler) -> None:
        if not spec.read_only:
            raise CapabilityError(
                f"Guardian capability '{spec.name}' must be read_only=True"
            )
        if spec.name in self._specs:
            raise CapabilityError(f"Capability '{spec.name}' already registered")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        logger.info(f"Guardian capability registered: {spec.name}")

    def list_specs(self) -> list[CapabilitySpec]:
        return list(self._specs.values())

    def get_spec(self, name: str) -> CapabilitySpec | None:
        return self._specs.get(name)

    async def invoke(self, name: str, inputs: dict | None = None) -> CapabilityResult:
        if name not in self._handlers:
            raise CapabilityNotFound(f"Unknown capability: {name}")
        handler = self._handlers[name]
        try:
            return await handler(inputs or {}, self._engine)
        except Exception as e:
            logger.exception(f"Guardian capability '{name}' failed")
            return CapabilityResult(
                capability=name,
                status="error",
                data={"error": str(e)},
                source_notes=[f"handler raised: {type(e).__name__}"],
            )

    def reset(self) -> None:
        """Clear all registrations. Intended for tests only."""
        self._specs.clear()
        self._handlers.clear()
        self._engine = None


_registry: CapabilityRegistry | None = None


def get_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        register_defaults(_registry)
    return _registry


def register_defaults(registry: CapabilityRegistry) -> None:
    """Wire the built-in Guardian capabilities."""
    from .qa_triage import QA_TRIAGE_SPEC, handle_qa_triage

    registry.register(QA_TRIAGE_SPEC, handle_qa_triage)
