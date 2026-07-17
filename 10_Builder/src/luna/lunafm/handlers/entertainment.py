"""
Entertainment channel — lateral creative thinking.

The handoff assumed a `cluster_id` column that doesn't exist in the
current schema. MVP substitute: sample two nodes of DIFFERENT
`node_type` with solid lock_in, and ask the LLM to find a structural
/ thematic / metaphorical connection.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CONNECTION_TYPES = ("STRUCTURAL", "THEMATIC", "METAPHORICAL", "CAUSAL", "TEMPORAL")


async def sample_nodes(station, channel, context: dict) -> dict:
    """
    Pick two nodes for creative synthesis.

    If the spectral engine has coordinates and curiosity is high,
    prefer a spectrally-close pair (V3 resonance). Otherwise fall
    back to random different-node_type sampling.
    """
    db = station.db
    if db is None:
        return {"signal": False}

    # Try spectral-aware sampling when curiosity is wide enough
    spectral = getattr(station, "_spectral", None)
    if spectral is not None and spectral.has_coords():
        try:
            from luna.lunafm.frequency_coupling import read_traits, trait_to_aperture
            traits = await read_traits(db)
            curiosity = float(traits.get("curiosity", 0.5))
            preset, sigma = trait_to_aperture(curiosity)
            if curiosity >= 0.5:
                # Pick any random node with spectral coords as seed
                seed_id = next(iter(spectral._coords.keys()), None)
                if seed_id:
                    pairs = spectral.find_resonance_pairs(seed_id, sigma=sigma, limit=3)
                    if pairs:
                        partner_id = pairs[0]["node_id"]
                        a = await db.fetchone(
                            "SELECT id, node_type, content FROM memory_nodes WHERE id = ?",
                            (seed_id,),
                        )
                        b = await db.fetchone(
                            "SELECT id, node_type, content FROM memory_nodes WHERE id = ?",
                            (partner_id,),
                        )
                        if a and b and a[1] != b[1]:
                            pair_key = f"pair:{a[0]}:{b[0]}"
                            if not channel._cache.seen_recently(pair_key):
                                channel._cache.touch(pair_key)
                                logger.debug(
                                    f"[LUNAFM:entertainment] spectral pair ({preset} σ={sigma:.2f})"
                                )
                                return {
                                    "signal": True,
                                    "node_a": {"id": a[0], "type": a[1], "content": a[2]},
                                    "node_b": {"id": b[0], "type": b[1], "content": b[2]},
                                    "source": "spectral",
                                }
        except Exception as e:
            logger.debug(f"[LUNAFM:entertainment] spectral sampling failed: {e}")

    # Fallback: random different-node_type sampling
    node_a = await db.fetchone(
        """
        SELECT id, node_type, content
        FROM memory_nodes
        WHERE node_type IN ('FACT', 'ENTITY', 'MEMORY', 'DECISION', 'INSIGHT', 'PREFERENCE')
          AND lock_in > 0.3
          AND (source IS NULL OR source NOT LIKE 'lunafm:%')
          AND length(content) > 20
        ORDER BY RANDOM() LIMIT 1
        """
    )
    if not node_a:
        return {"signal": False}

    node_b = await db.fetchone(
        """
        SELECT id, node_type, content
        FROM memory_nodes
        WHERE node_type IN ('FACT', 'ENTITY', 'MEMORY', 'DECISION', 'INSIGHT', 'PREFERENCE')
          AND lock_in > 0.3
          AND (source IS NULL OR source NOT LIKE 'lunafm:%')
          AND node_type != ?
          AND id != ?
          AND length(content) > 20
        ORDER BY RANDOM() LIMIT 1
        """,
        (node_a[1], node_a[0]),
    )
    if not node_b:
        return {"signal": False}

    pair_key = f"pair:{node_a[0]}:{node_b[0]}"
    rev_key = f"pair:{node_b[0]}:{node_a[0]}"
    if channel._cache.seen_recently(pair_key) or channel._cache.seen_recently(rev_key):
        return {"signal": False}
    channel._cache.touch(pair_key)

    return {
        "signal": True,
        "node_a": {"id": node_a[0], "type": node_a[1], "content": node_a[2]},
        "node_b": {"id": node_b[0], "type": node_b[1], "content": node_b[2]},
    }


async def find_connection(station, channel, context: dict) -> dict:
    """Creative LLM call — temp 0.9."""
    llm = station.llm
    a = context.get("node_a")
    b = context.get("node_b")
    if llm is None or not a or not b:
        return {"emit": False}

    prompt = (
        "You are Luna's creative subconscious. Two memories from different parts "
        "of your experience are below. Find a GENUINE structural, thematic, or "
        "metaphorical connection between them — not forced, not superficial.\n\n"
        f"Memory A ({a['type']}):\n{(a['content'] or '')[:300]}\n\n"
        f"Memory B ({b['type']}):\n{(b['content'] or '')[:300]}\n\n"
        "If there's a real connection, describe it in 2-3 sentences. Start the "
        "response with the connection type:\n\n"
        "STRUCTURAL: same underlying pattern / architecture\n"
        "THEMATIC: same human question from different angles\n"
        "METAPHORICAL: one illuminates the other through analogy\n"
        "CAUSAL: one might influence or explain the other\n"
        "TEMPORAL: different moments in the same arc\n\n"
        "If there's no genuine connection, respond exactly: NO_CONNECTION"
    )

    from luna.llm.base import Message as LLMMessage

    try:
        result = await llm.complete(
            messages=[
                LLMMessage(role="system", content="You are Luna's creative subconscious."),
                LLMMessage(role="user", content=prompt),
            ],
            temperature=0.9,
            max_tokens=220,
            model=station.default_model,
        )
    except Exception as e:
        logger.debug(f"[LUNAFM:entertainment] LLM call failed: {e}")
        return {"emit": False}

    text = (result.content or "").strip()
    if not text or text.upper().startswith("NO_CONNECTION"):
        return {"emit": False}

    ctype = "STRUCTURAL"
    for t in CONNECTION_TYPES:
        if text.upper().startswith(t):
            ctype = t
            break

    return {"emit": True, "connection": text, "connection_type": ctype}


async def assess_novelty(station, channel, context: dict) -> dict:
    """Skip if an edge already exists between the two nodes."""
    db = station.db
    a = context.get("node_a")
    b = context.get("node_b")
    if db is None or not a or not b:
        return {"emit": False}

    row = await db.fetchone(
        """
        SELECT COUNT(*) FROM graph_edges
        WHERE (from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?)
        """,
        (a["id"], b["id"], b["id"], a["id"]),
    )
    existing = row[0] if row else 0
    if existing > 0:
        return {"emit": False}

    return {"emit": True}


async def write_synthesis(station, channel, context: dict) -> dict:
    """Write SYNTHESIS node + SYNTHESIZED_CONNECTION edge."""
    connection = context.get("connection")
    a = context.get("node_a")
    b = context.get("node_b")
    if not connection or not a or not b:
        return {"artifact_written": False}

    node_id = await station.write_artifact(
        channel_id="entertainment",
        node_type="SYNTHESIS",
        content=connection,
        lock_in=0.2,
        importance=0.4,
        metadata={
            "node_a_id": a["id"],
            "node_a_type": a["type"],
            "node_b_id": b["id"],
            "node_b_type": b["type"],
            "connection_type": context.get("connection_type"),
            "channel": "entertainment",
        },
    )

    # Best-effort graph edge (UNIQUE constraint means duplicates just no-op)
    db = station.db
    if db is not None and node_id:
        try:
            from datetime import datetime
            await db.execute(
                """
                INSERT OR IGNORE INTO graph_edges
                    (from_id, to_id, relationship, strength, created_at, origin)
                VALUES (?, ?, 'SYNTHESIZED_CONNECTION', 0.3, ?, 'lunafm:entertainment')
                """,
                (a["id"], b["id"], datetime.utcnow().isoformat()),
            )
        except Exception as e:
            logger.debug(f"[LUNAFM:entertainment] edge write failed: {e}")

    return {"artifact_written": bool(node_id), "node_id": node_id}
