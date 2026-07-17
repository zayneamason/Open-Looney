"""Local adapter base contract for Lexicon local-inference roles."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..telemetry import LexiconFailure, emit_failure


class LocalAdapter(ABC):
    """Base class for Lexicon local adapters.

    Adapters must normalize their own failures to ``None`` and emit a
    :class:`LexiconFailure` record. Exceptions must not escape Lexicon's
    public API. Local roles (e.g. ``embed``) keep their public sync
    signature; this base mirrors :class:`RemoteAdapter` discipline without
    inheriting its message/schema helpers.
    """

    adapter_name: str = "local"

    def __init__(self) -> None:
        self._refcount: int = 0

    def load(self) -> None:
        """Default lifecycle — increments logical refcount.

        Subclasses that bind a model handle should override and call
        ``super().load()`` to keep refcount housekeeping in one place.
        """
        self._refcount += 1

    def unload(self) -> None:
        """Default lifecycle — decrements logical refcount, floor at zero.

        Subclasses that release a model handle should override and call
        ``super().unload()`` for the refcount housekeeping.
        """
        self._refcount = max(0, self._refcount - 1)

    @abstractmethod
    def infer(self, text: str) -> Optional[Any]:
        """Run local inference; return ``None`` on failure."""

    def _emit_failure(self, role: str, error_code: str, **call_meta: Any) -> None:
        """Best-effort structured failure telemetry."""
        filtered_meta = {
            key: value for key, value in call_meta.items() if value is not None
        }
        try:
            emit_failure(
                LexiconFailure(
                    role=role,
                    error_code=error_code,
                    call_meta={"adapter": self.adapter_name, **filtered_meta},
                )
            )
        except Exception:
            # Telemetry must never raise into runtime callers.
            pass
