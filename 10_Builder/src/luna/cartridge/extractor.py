"""
Cartridge Extractor
===================

LLM-based extraction pass for .lun cartridges.
Walks sections, sends text to Haiku, writes claims/entities/summaries
with source anchoring back to specific nodes.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# SPEC-001: identifier written to claim_sources.anchored_by for builder-produced anchors.
ANCHORED_BY = "builder@v0.2"


def _now_ms() -> int:
    """SPEC-001 timestamp helper — unix milliseconds for claim_sources.anchored_at."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _extract_logprob_signal(result) -> tuple[Optional[float], Optional[int]]:
    """SPEC-003: extract response-level logprob signal from a Haiku backend result.

    Returns (llm_logprob_sum, llm_token_count). Paired tuple maintains the
    paired-NULL invariant. NEVER fabricate — return (None, None) when signal absent.
    """
    # Current environment: HaikuResult exposes text only. Signal is absent.
    # Keep this defensive path so future backend fields can be consumed safely.
    usage = getattr(result, "usage", None)
    if usage is None:
        return None, None

    lp_sum = getattr(usage, "output_logprobs_sum", None)
    tokens = getattr(usage, "output_tokens", None)
    if lp_sum is None or tokens is None:
        return None, None

    # SPEC-003 requires natural-log storage. If backend reports log10, convert.
    base = str(getattr(usage, "output_logprob_base", "e")).lower()
    lp_sum_f = float(lp_sum)
    if base in ("e", "ln"):
        pass
    elif base in ("10", "log10"):
        lp_sum_f = lp_sum_f * 2.302585092994046  # ln(10)
    else:
        return None, None

    if int(tokens) <= 0:
        return None, None
    return lp_sum_f, int(tokens)

CARTRIDGE_EXTRACTION_PROMPT = """\
You are extracting structured knowledge from a document section
for a long-term memory system.

Extract the following from the provided text:

1. SUMMARY: A 1-2 sentence summary of what this section covers.

2. CLAIMS: Key arguments, assertions, or findings.
   For each claim, include a short verbatim quote from the source
   that supports it. Include 3-10 claims per section.

3. ENTITIES: People, places, systems, organizations, and concepts
   mentioned. For each entity, note its type.

Return ONLY valid JSON:
{
  "summary": "Section summary...",
  "claims": [
    {"content": "The claim in your own words", "quote": "exact words from the source"}
  ],
  "entities": [
    {"name": "Entity Name", "type": "person|place|organization|concept|event"}
  ]
}

RULES:
- Be specific: include names, numbers, dates when present
- Claims must be attributable to the text
- Quotes must be verbatim substrings from the source text
- If the text is a title page, table of contents, or index,
  return {"summary": "", "claims": [], "entities": []}
- Return ONLY JSON. No markdown, no explanation.
"""


class CartridgeExtractor:
    """Extract claims, entities, and summaries from a .lun node tree."""

    def __init__(self):
        self._backend = None

    def _get_backend(self):
        if self._backend is None:
            from luna.inference.haiku_subtask_backend import HaikuSubtaskBackend
            self._backend = HaikuSubtaskBackend()
        return self._backend

    @property
    def is_available(self) -> bool:
        try:
            return self._get_backend().is_loaded
        except Exception:
            return False

    async def extract(self, conn: sqlite3.Connection, ulid_ctx: dict | None = None) -> int:
        """Run extraction on all sections. Returns count of extractions created.

        SPEC-002: ulid_ctx carries the per-build ULIDGenerator and the
        node_id→ulid map produced by the builder. Required in v0.2; absence is
        treated as a caller bug rather than a runtime fallback (no silent
        degradation — every extraction row must have a ulid)."""
        if ulid_ctx is None:
            raise RuntimeError("extractor.extract() requires ulid_ctx in v0.2 schema")
        ulid_gen = ulid_ctx["gen"]
        node_id_to_ulid = ulid_ctx["node_id_to_ulid"]

        backend = self._get_backend()
        if not backend.is_loaded:
            logger.warning("[CARTRIDGE-EXTRACTOR] Haiku backend not available — skipping extraction")
            return 0

        # Gather sections with descendant text
        sections = conn.execute(
            "SELECT id, content FROM doc_nodes WHERE type = 'section'"
        ).fetchall()

        total_extractions = 0

        for section_id, section_heading in sections:
            # Get all sentence/paragraph content under this section
            descendants = conn.execute(
                """
                WITH RECURSIVE subtree AS (
                    SELECT id, content, type FROM doc_nodes WHERE id = ?
                    UNION ALL
                    SELECT d.id, d.content, d.type FROM doc_nodes d
                    JOIN subtree s ON d.parent_id = s.id
                )
                SELECT id, content, type FROM subtree
                WHERE type IN ('sentence', 'list_item', 'cell')
                AND content IS NOT NULL AND content != ''
                ORDER BY id
                """,
                (section_id,),
            ).fetchall()

            if not descendants:
                continue

            section_text = "\n".join(row[1] for row in descendants)
            if len(section_text.strip()) < 50:
                continue

            # Call Haiku
            try:
                user_msg = f"Section: {section_heading or 'Untitled'}\n\nTEXT:\n{section_text[:8000]}"
                result = await backend.generate(
                    user_message=user_msg,
                    system_prompt=CARTRIDGE_EXTRACTION_PROMPT,
                    max_tokens=4096,
                )

                # Parse JSON
                raw = result.text.strip()
                raw = re.sub(r"^```json\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                data = json.loads(raw)

                # SPEC-003: capture response-level logprob signal. Paired-NULL
                # unless backend exposes usage.output_logprobs_sum + output_tokens.
                llm_logprob_sum, llm_token_count = _extract_logprob_signal(result)

            except json.JSONDecodeError as e:
                logger.warning("[CARTRIDGE-EXTRACTOR] JSON parse failed for section '%s': %s", section_heading, e)
                continue
            except Exception as e:
                logger.warning("[CARTRIDGE-EXTRACTOR] Haiku call failed for section '%s': %s", section_heading, e)
                continue

            # Build sentence lookup for anchoring. SPEC-002: widened to 3-tuple
            # (node_id, content, node_ulid). KeyError on a missing mapping is the
            # correct signal (no silent NULL fallback per spec).
            sentence_nodes = [
                (row[0], row[1], node_id_to_ulid[row[0]])
                for row in descendants if row[2] == "sentence"
            ]

            # Write summary — SPEC-001: anchored to section heading node.
            summary = data.get("summary", "")
            if summary:
                summary_ulid = ulid_gen.next()
                cursor = conn.execute(
                    "INSERT INTO extractions "
                    "(type, content, anchor_status, ulid, "
                    " llm_logprob_sum, llm_token_count, extraction_method) "
                    "VALUES (?, ?, 'anchored', ?, ?, ?, 'llm')",
                    ("summary", summary, summary_ulid, llm_logprob_sum, llm_token_count),
                )
                summary_id = cursor.lastrowid
                section_ulid = node_id_to_ulid[section_id]
                conn.execute(
                    "INSERT INTO claim_sources "
                    "(claim_id, node_id, anchor_method, anchored_by, anchored_at, claim_ulid, node_ulid) "
                    "VALUES (?, ?, 'auto', ?, ?, ?, ?)",
                    (summary_id, section_id, ANCHORED_BY, _now_ms(), summary_ulid, section_ulid),
                )
                total_extractions += 1

            # Write claims — SPEC-001: every claim lands in 'anchored' or 'match_failed'.
            for claim in data.get("claims", []):
                content = claim.get("content", "")
                quote = claim.get("quote", "")
                if not content:
                    continue

                claim_ulid = ulid_gen.next()
                cursor = conn.execute(
                    "INSERT INTO extractions "
                    "(type, content, ulid, llm_logprob_sum, llm_token_count, extraction_method) "
                    "VALUES (?, ?, ?, ?, ?, 'llm')",
                    ("claim", content, claim_ulid, llm_logprob_sum, llm_token_count),
                )
                claim_id = cursor.lastrowid
                total_extractions += 1

                success, reason = self._anchor_claim(
                    conn, claim_id, claim_ulid, quote, sentence_nodes, anchored_by=ANCHORED_BY,
                )
                if success:
                    conn.execute(
                        "UPDATE extractions SET anchor_status = 'anchored' WHERE id = ?",
                        (claim_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE extractions SET anchor_status = 'match_failed', "
                        "anchor_reason = ? WHERE id = ?",
                        (reason, claim_id),
                    )

            # Write entities — SPEC-001: anchor_status='unknown' is permitted (spec scope is claims).
            for entity in data.get("entities", []):
                name = entity.get("name", "")
                etype = entity.get("type", "concept")
                if not name:
                    continue

                entity_ulid = ulid_gen.next()
                conn.execute(
                    "INSERT INTO extractions "
                    "(type, content, anchor_status, anchor_reason, ulid, "
                    " llm_logprob_sum, llm_token_count, extraction_method) "
                    "VALUES (?, ?, 'unknown', ?, ?, ?, ?, 'llm')",
                    ("entity",
                     f"{name} [{etype}]",
                     "entity anchoring not implemented in v0.2",
                     entity_ulid, llm_logprob_sum, llm_token_count),
                )
                total_extractions += 1

        conn.commit()
        # SPEC-001 Step 2c: emit anchor classification distribution.
        status_counts = dict(conn.execute(
            "SELECT anchor_status, COUNT(*) FROM extractions GROUP BY anchor_status"
        ).fetchall())
        logger.info("[CARTRIDGE-EXTRACTOR] Anchor classification: %s", status_counts)
        logger.info("[CARTRIDGE-EXTRACTOR] Created %d extractions across %d sections", total_extractions, len(sections))
        return total_extractions

    def _anchor_claim(
        self,
        conn: sqlite3.Connection,
        claim_id: int,
        claim_ulid: str,
        quote: str,
        sentence_nodes: list[tuple[int, str, str]],
        anchored_by: str,
    ) -> tuple[bool, str]:
        """SPEC-001: fuzzy-match a quote to sentence nodes and write claim_sources
        with provenance. Returns (success, reason). reason is empty on success;
        describes the failure mode otherwise so extract() can record anchor_reason.

        SPEC-002: claim_ulid + per-sentence node_ulid are written to the
        claim_sources shadow columns on every successful match."""
        if not quote:
            return False, "no quote in extraction"

        now_ms = _now_ms()
        quote_lower = quote.lower()

        # Substring match
        for node_id, sentence_content, node_ulid in sentence_nodes:
            if sentence_content and quote_lower in sentence_content.lower():
                conn.execute(
                    "INSERT OR IGNORE INTO claim_sources "
                    "(claim_id, node_id, anchor_method, anchored_by, anchored_at, claim_ulid, node_ulid) "
                    "VALUES (?, ?, 'auto', ?, ?, ?, ?)",
                    (claim_id, node_id, anchored_by, now_ms, claim_ulid, node_ulid),
                )
                return True, ""

        # Prefix fallback (first 40 chars)
        prefix = quote_lower[:40]
        if len(prefix) < 10:
            return False, f"quote too short for prefix fallback ({len(prefix)} chars)"
        for node_id, sentence_content, node_ulid in sentence_nodes:
            if sentence_content and prefix in sentence_content.lower():
                conn.execute(
                    "INSERT OR IGNORE INTO claim_sources "
                    "(claim_id, node_id, anchor_method, anchored_by, anchored_at, claim_ulid, node_ulid) "
                    "VALUES (?, ?, 'auto', ?, ?, ?, ?)",
                    (claim_id, node_id, anchored_by, now_ms, claim_ulid, node_ulid),
                )
                return True, ""

        return False, f"no substring or prefix match for quote: {quote[:60]!r}"
