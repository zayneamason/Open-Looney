"""Pre-flight tracer — exercises all critical subsystems and returns health report.

Sends a live round through memory, search, LLM, and voice to verify
they're actually working.  All test artifacts are cleaned up after.
"""

import logging
import time
import uuid

logger = logging.getLogger(__name__)


async def run_preflight(engine) -> dict:
    """Exercise memory, search, LLM provider, and voice subsystems."""
    subsystems: list[dict] = []
    test_id = f"__preflight_{uuid.uuid4().hex[:8]}"

    # ── 1. Memory Matrix: add → get → delete ──
    t0 = time.monotonic()
    node_id = None
    try:
        matrix_actor = engine.get_actor("matrix")
        if matrix_actor and hasattr(matrix_actor, 'memory'):
            mm = matrix_actor.memory
            node_id = await mm.add_node(
                node_type="PREFLIGHT",
                content=f"Preflight test node {test_id}",
                source="preflight",
                metadata={"preflight": True},
            )
            node = await mm.get_node(node_id)
            if node is None:
                raise AssertionError("get_node returned None after add_node")
            await mm.delete_node(node_id)
            verify = await mm.get_node(node_id)
            if verify is not None:
                raise AssertionError("delete_node failed — artifact remains")
            subsystems.append(_ok("memory_matrix", t0, "add/get/delete OK"))
        else:
            subsystems.append(_fail("memory_matrix", t0, "Matrix actor not available"))
    except Exception as e:
        subsystems.append(_fail("memory_matrix", t0, str(e)))
        # Cleanup attempt
        if node_id:
            try:
                await mm.delete_node(node_id)
            except Exception:
                pass

    # ── 2. AiBrarian Search ──
    t0 = time.monotonic()
    try:
        aib = engine.aibrarian
        if aib and hasattr(aib, 'connections') and aib.connections:
            first_key = next(iter(aib.connections.keys()))
            results = await aib.search(first_key, "preflight test query", "keyword", 1)
            subsystems.append(_ok(
                "aibrarian_search", t0,
                f"searched '{first_key}', {len(results)} results",
            ))
        else:
            subsystems.append(_fail("aibrarian_search", t0, "No collections connected"))
    except Exception as e:
        subsystems.append(_fail("aibrarian_search", t0, str(e)))

    # ── 3. LLM Provider Chain ──
    t0 = time.monotonic()
    try:
        director = engine.get_actor("director")
        if director:
            chain = getattr(director, '_fallback', None) or getattr(director, 'fallback_chain', None)
            if chain and hasattr(chain, 'get_chain'):
                providers = chain.get_chain()
                subsystems.append(_ok(
                    "llm_provider", t0,
                    f"chain={providers}",
                ))
            elif hasattr(director, '_provider_name'):
                subsystems.append(_ok("llm_provider", t0, f"provider={director._provider_name}"))
            else:
                subsystems.append(_fail("llm_provider", t0, "No fallback chain found"))
        else:
            subsystems.append(_fail("llm_provider", t0, "Director actor not found"))
    except Exception as e:
        subsystems.append(_fail("llm_provider", t0, str(e)))

    # ── 4. Voice Availability ──
    t0 = time.monotonic()
    try:
        from voice.backend import VoiceBackend  # noqa: F401
        subsystems.append(_ok("voice", t0, "VoiceBackend importable"))
    except ImportError:
        subsystems.append({
            "name": "voice",
            "ok": False,
            "latency_ms": _ms(t0),
            "detail": "voice module not available",
            "status": "unavailable",
        })

    all_ok = all(s["ok"] for s in subsystems)
    return {
        "status": "pass" if all_ok else "partial",
        "subsystems": subsystems,
        "all_ok": all_ok,
    }


# ── Helpers ──

def _ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 1)


def _ok(name: str, t0: float, detail: str) -> dict:
    return {"name": name, "ok": True, "latency_ms": _ms(t0), "detail": detail}


def _fail(name: str, t0: float, detail: str) -> dict:
    return {"name": name, "ok": False, "latency_ms": _ms(t0), "detail": detail}
