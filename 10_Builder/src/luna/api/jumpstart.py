"""Jumpstart — restart degraded subsystems and verify via preflight.

Soft-reboots the cognitive layer without restarting the server or wiping data.
"""

import logging

logger = logging.getLogger(__name__)


async def run_jumpstart(engine) -> dict:
    """Restart actors, reconnect collections, reset fallback, then preflight."""
    actions: list[dict] = []

    # ── 1. Restart Scribe + Librarian actors ──
    for actor_name in ("scribe", "librarian"):
        try:
            actor = engine.get_actor(actor_name)
            if actor:
                if hasattr(actor, 'reset'):
                    await actor.reset()
                    actions.append({"action": f"restart_{actor_name}", "ok": True})
                else:
                    # Actor exists but has no reset method — just acknowledge
                    actions.append({"action": f"restart_{actor_name}", "ok": True, "detail": "actor present, no reset method"})
            else:
                actions.append({"action": f"restart_{actor_name}", "ok": False, "detail": "actor not registered"})
        except Exception as e:
            actions.append({"action": f"restart_{actor_name}", "ok": False, "detail": str(e)})

    # ── 2. Reconnect AiBrarian collections ──
    try:
        aib = engine.aibrarian
        if aib:
            await aib.initialize()
            connected = list(aib.connections.keys()) if hasattr(aib, 'connections') and aib.connections else []
            actions.append({"action": "reconnect_collections", "ok": True, "detail": f"connected={connected}"})
        else:
            actions.append({"action": "reconnect_collections", "ok": False, "detail": "No AiBrarian engine"})
    except Exception as e:
        actions.append({"action": "reconnect_collections", "ok": False, "detail": str(e)})

    # ── 3. Rotate LLM fallback chain (move failed provider to end) ──
    try:
        director = engine.get_actor("director")
        if director:
            chain = getattr(director, '_fallback', None) or getattr(director, 'fallback_chain', None)
            if chain and hasattr(chain, 'get_chain'):
                current = chain.get_chain()
                # If first provider has recent failures, rotate it to the end
                stats = getattr(chain, 'stats', None)
                if stats and hasattr(stats, 'provider_stats'):
                    first = current[0] if current else None
                    pstats = stats.provider_stats.get(first, {})
                    if pstats.get('consecutive_failures', 0) > 0 and len(current) > 1:
                        rotated = current[1:] + [current[0]]
                        chain.set_chain(rotated)
                        actions.append({"action": "rotate_fallback", "ok": True, "detail": f"rotated: {current} → {rotated}"})
                    else:
                        actions.append({"action": "rotate_fallback", "ok": True, "detail": f"chain healthy: {current}"})
                else:
                    actions.append({"action": "rotate_fallback", "ok": True, "detail": f"chain={current}"})
            else:
                actions.append({"action": "rotate_fallback", "ok": True, "detail": "no fallback chain to rotate"})
        else:
            actions.append({"action": "rotate_fallback", "ok": False, "detail": "Director not found"})
    except Exception as e:
        actions.append({"action": "rotate_fallback", "ok": False, "detail": str(e)})

    # ── 4. Run preflight to verify ──
    from luna.api.preflight import run_preflight
    preflight = await run_preflight(engine)

    return {
        "actions": actions,
        "preflight": preflight,
        "all_ok": preflight["all_ok"],
    }
