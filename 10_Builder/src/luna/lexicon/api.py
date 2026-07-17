"""Lexicon public API — System Spec v1.2 Section 6.2.

Phase E.1: `generate` and `curate` are backed by remote adapters.
Phase E.2: `embed` is backed by a local MiniLM adapter; the remaining
local roles still return stubbed `None` + telemetry.
Role-level exceptions never cross this boundary.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional, Sequence

from .adapters import (
    ClassifyIntentAdapter,
    ClassifySafetyAdapter,
    ClaudeAdapter,
    DetectLanguageAdapter,
    EmbedAdapter,
    LocalAdapter,
    NERAdapter,
    OllamaAdapter,
    RemoteAdapter,
    RerankAdapter,
)
from .registry import get_registry
from .telemetry import LexiconFailure, emit_failure

_REMOTE_ADAPTERS: Optional[list[RemoteAdapter]] = None
_EMBED_ADAPTER: Optional[EmbedAdapter] = None
_RERANK_ADAPTER: Optional[RerankAdapter] = None
_DETECT_LANGUAGE_ADAPTER: Optional[DetectLanguageAdapter] = None
_CLASSIFY_INTENT_ADAPTER: Optional[ClassifyIntentAdapter] = None
_CLASSIFY_SAFETY_ADAPTER: Optional[ClassifySafetyAdapter] = None
_NER_ADAPTER: Optional[LocalAdapter] = None


def _get_remote_adapters() -> list[RemoteAdapter]:
    global _REMOTE_ADAPTERS
    if _REMOTE_ADAPTERS is None:
        _REMOTE_ADAPTERS = [ClaudeAdapter(), OllamaAdapter()]
    return _REMOTE_ADAPTERS


def _get_embed_adapter() -> EmbedAdapter:
    # Process-lifetime singleton. Deliberately does not call ``load()``: no
    # E.2 caller invokes ``unload()``, so refcount balancing is not useful
    # here. Lazy-bind in :meth:`EmbedAdapter.infer` covers the model handle.
    global _EMBED_ADAPTER
    if _EMBED_ADAPTER is None:
        _EMBED_ADAPTER = EmbedAdapter()
    return _EMBED_ADAPTER


def _get_rerank_adapter() -> RerankAdapter:
    global _RERANK_ADAPTER
    if _RERANK_ADAPTER is None:
        _RERANK_ADAPTER = RerankAdapter()
    return _RERANK_ADAPTER


def _get_detect_language_adapter() -> LocalAdapter:
    """Bind the detect_language adapter declared by the registry.

    Phase F.2: ``implementation: model`` selects
    :class:`FasttextDetectLanguageAdapter`; everything else selects the
    heuristic :class:`DetectLanguageAdapter`. No silent fallback on load
    failure — api boundary surfaces ``adapter_internal_error``.
    """
    global _DETECT_LANGUAGE_ADAPTER
    if _DETECT_LANGUAGE_ADAPTER is None:
        registry = get_registry()
        entry = registry.get_role("detect_language")
        kind = entry.implementation if entry else ""
        if kind == "model":
            from .adapters import FasttextDetectLanguageAdapter

            _DETECT_LANGUAGE_ADAPTER = FasttextDetectLanguageAdapter()
        else:
            _DETECT_LANGUAGE_ADAPTER = DetectLanguageAdapter()
    return _DETECT_LANGUAGE_ADAPTER


def _get_classify_intent_adapter() -> LocalAdapter:
    """Bind the classify_intent adapter declared by the registry.

    Phase F.5: ``implementation: model`` selects :class:`BartIntentAdapter`;
    everything else selects the heuristic :class:`ClassifyIntentAdapter`. No
    silent fallback on load failure — api boundary surfaces
    ``adapter_internal_error``.
    """
    global _CLASSIFY_INTENT_ADAPTER
    if _CLASSIFY_INTENT_ADAPTER is None:
        registry = get_registry()
        entry = registry.get_role("classify_intent")
        kind = entry.implementation if entry else ""
        if kind == "model":
            from .adapters import BartIntentAdapter

            _CLASSIFY_INTENT_ADAPTER = BartIntentAdapter()
        else:
            _CLASSIFY_INTENT_ADAPTER = ClassifyIntentAdapter()
    return _CLASSIFY_INTENT_ADAPTER


def _get_classify_safety_adapter() -> LocalAdapter:
    """Bind the classify_safety adapter declared by the registry.

    Phase F.3: ``implementation: model`` selects
    :class:`ToxicBertSafetyAdapter`; everything else selects the heuristic
    :class:`ClassifySafetyAdapter`. No silent fallback on load failure —
    api boundary surfaces ``adapter_internal_error``.
    """
    global _CLASSIFY_SAFETY_ADAPTER
    if _CLASSIFY_SAFETY_ADAPTER is None:
        registry = get_registry()
        entry = registry.get_role("classify_safety")
        kind = entry.implementation if entry else ""
        if kind == "model":
            from .adapters import ToxicBertSafetyAdapter

            _CLASSIFY_SAFETY_ADAPTER = ToxicBertSafetyAdapter()
        else:
            _CLASSIFY_SAFETY_ADAPTER = ClassifySafetyAdapter()
    return _CLASSIFY_SAFETY_ADAPTER


def _get_ner_adapter() -> LocalAdapter:
    """Bind the NER adapter declared by the registry's ``implementation`` field.

    Phase E.9: ``implementation: model`` selects :class:`SpacyNERAdapter`;
    everything else (``"heuristic"``, ``""``, or any unknown value) selects
    the heuristic :class:`NERAdapter`. There is no silent fallback: if the
    selected real-model adapter fails to load, the api boundary surfaces
    ``adapter_internal_error``; the heuristic is **not** retried.
    """
    global _NER_ADAPTER
    if _NER_ADAPTER is None:
        registry = get_registry()
        entry = registry.get_role("ner")
        kind = entry.implementation if entry else ""
        if kind == "model":
            from .adapters import SpacyNERAdapter

            _NER_ADAPTER = SpacyNERAdapter()
        else:
            _NER_ADAPTER = NERAdapter()
    return _NER_ADAPTER


def _stub_role(role: str, **call_meta: Any) -> None:
    try:
        record = LexiconFailure(
            role=role,
            error_code="role_unimplemented",
            call_meta={k: v for k, v in call_meta.items() if v is not None},
        )
        emit_failure(record)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role=role,
                    error_code="role_internal_error",
                    call_meta={"exception_type": type(exc).__name__},
                )
            )
        except Exception:
            pass
    return None


def embed(text: str) -> Optional[Any]:
    try:
        return _get_embed_adapter().infer(text)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="embed",
                    error_code="adapter_internal_error",
                    call_meta={
                        "text_len": len(text) if isinstance(text, str) else None,
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            pass
    return None


def rerank(query: str, candidates: Sequence[Any]) -> Optional[List[Any]]:
    try:
        return _get_rerank_adapter().rerank(query, candidates)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="rerank",
                    error_code="adapter_internal_error",
                    call_meta={
                        "query_len": len(query) if isinstance(query, str) else None,
                        "candidate_count": len(candidates)
                        if hasattr(candidates, "__len__")
                        else None,
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            pass
    return None


def ner(text: str) -> Optional[List[Any]]:
    try:
        return _get_ner_adapter().ner(text)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="ner",
                    error_code="adapter_internal_error",
                    call_meta={
                        "text_len": len(text) if isinstance(text, str) else None,
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            pass
    return None


def classify_safety(text: str) -> Optional[Any]:
    try:
        return _get_classify_safety_adapter().classify_safety(text)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="classify_safety",
                    error_code="adapter_internal_error",
                    call_meta={
                        "text_len": len(text) if isinstance(text, str) else None,
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            pass
    return None


def classify_intent(text: str) -> Optional[str]:
    try:
        return _get_classify_intent_adapter().classify_intent(text)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="classify_intent",
                    error_code="adapter_internal_error",
                    call_meta={
                        "text_len": len(text) if isinstance(text, str) else None,
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            pass
    return None


def detect_language(text: str) -> Optional[str]:
    try:
        return _get_detect_language_adapter().detect_language(text)
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="detect_language",
                    error_code="adapter_internal_error",
                    call_meta={
                        "text_len": len(text) if isinstance(text, str) else None,
                        "exception_type": type(exc).__name__,
                    },
                )
            )
        except Exception:
            pass
    return None


async def curate(
    messages: Sequence[Mapping[str, Any]],
    schema: Any,
    *,
    cache_breakpoints: Optional[Iterable[Any]] = None,
) -> Optional[Any]:
    try:
        for adapter in _get_remote_adapters():
            result = await adapter.curate(
                messages,
                schema,
                cache_breakpoints=cache_breakpoints,
            )
            if result is not None:
                return result

        emit_failure(
            LexiconFailure(
                role="curate",
                error_code="all_adapters_returned_none",
                call_meta={
                    "message_count": len(messages)
                    if hasattr(messages, "__len__")
                    else None,
                    "has_schema": schema is not None,
                    "cache_breakpoints_set": cache_breakpoints is not None,
                },
            )
        )
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="curate",
                    error_code="adapter_internal_error",
                    call_meta={
                        "exception_type": type(exc).__name__,
                        "cache_breakpoints_set": cache_breakpoints is not None,
                    },
                )
            )
        except Exception:
            pass
    return None


async def generate(
    messages: Sequence[Mapping[str, Any]],
    *,
    stream: bool = True,
    cache_breakpoints: Optional[Iterable[Any]] = None,
) -> Optional[Any]:
    try:
        for adapter in _get_remote_adapters():
            result = await adapter.generate(
                messages,
                stream=stream,
                cache_breakpoints=cache_breakpoints,
            )
            if result is not None:
                return result

        emit_failure(
            LexiconFailure(
                role="generate",
                error_code="all_adapters_returned_none",
                call_meta={
                    "message_count": len(messages)
                    if hasattr(messages, "__len__")
                    else None,
                    "stream": stream,
                    "cache_breakpoints_set": cache_breakpoints is not None,
                },
            )
        )
    except Exception as exc:
        try:
            emit_failure(
                LexiconFailure(
                    role="generate",
                    error_code="adapter_internal_error",
                    call_meta={
                        "exception_type": type(exc).__name__,
                        "stream": stream,
                        "cache_breakpoints_set": cache_breakpoints is not None,
                    },
                )
            )
        except Exception:
            pass
    return None
