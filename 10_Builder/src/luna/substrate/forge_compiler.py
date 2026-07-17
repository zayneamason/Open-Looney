"""
Forge Compiler — multi-pass document comprehension.

Replaces single-pass Haiku extraction with 5 focused reading passes,
each building on the previous one's output.  The orchestrator adapts
the pass plan based on document type detected in Pass 1.

Passes:
  1. Structure   — skeleton (headings, type, hierarchy)
  2. Topics      — section summaries, themes, domain
  3. Entities    — named entities + relationships
  4. Claims      — arguments, evidence, contradictions
  5. Synthesis   — summary, insights, Matrix promotion candidates
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pass prompts
# ---------------------------------------------------------------------------

STRUCTURE_PROMPT = """\
Analyze the structure of this document.
Return ONLY valid JSON with this shape:
{
  "doc_type": "research_paper|report|legal|interview|notes|manual|reference|narrative",
  "title": "document title",
  "sections": [
    {
      "heading": "section name",
      "level": 1,
      "start_offset": 0,
      "subsections": []
    }
  ],
  "page_count_estimate": 42,
  "language": "en"
}
Do NOT analyze content. Only map structure."""

TOPICS_PROMPT = """\
You have this structural map of a document:
{structure_json}

Here is the document text (may be truncated):
{doc_text}

For each section, identify:
- Primary topic (1-3 words)
- Domain (governance, ecology, technology, culture, economics, etc.)
- 1-2 sentence summary of what the section covers
- Key themes that run across sections

Return ONLY valid JSON:
{{
  "sections": [
    {{"heading": "...", "topic": "...", "domain": "...", "summary": "..."}}
  ],
  "themes": ["theme1", "theme2"],
  "primary_domain": "..."
}}"""

ENTITIES_PROMPT = """\
You have this document about {primary_domain}.
Structural map: {structure_json}
Topic index: {topics_json}

Here is the document text (may be truncated):
{doc_text}

Extract all named entities and their relationships.
For each entity note which sections reference it.

Return ONLY valid JSON:
{{
  "entities": [
    {{
      "name": "...",
      "type": "person|place|organization|concept|event",
      "description": "1 sentence",
      "sections": [0, 2, 5]
    }}
  ],
  "relationships": [
    {{
      "from": "entity name",
      "to": "entity name",
      "type": "INVOLVES|MENTIONS|CO_OCCURS|GOVERNS|CONTRADICTS",
      "context": "1 sentence"
    }}
  ]
}}"""

CLAIMS_PROMPT = """\
You have this {doc_type} about {primary_domain}.
Key entities: {entity_names}
Sections: {section_summaries}

Here is the document text (may be truncated):
{doc_text}

For each section, extract specific claims the document makes.
A claim is a verifiable assertion, argument, or conclusion.

Return ONLY valid JSON:
{{
  "claims": [
    {{
      "claim": "the specific claim",
      "type": "premise|evidence|conclusion|observation",
      "section": "section heading",
      "confidence": 0.85,
      "entities_involved": ["entity1", "entity2"],
      "supporting_evidence": "brief quote or reference",
      "contradicts": null
    }}
  ]
}}"""

SYNTHESIS_PROMPT = """\
You have analyzed a {doc_type} about {primary_domain}.
Structure: {structure_json}
Topics: {topics_json}
Entities: {entities_json}
Claims: {claims_json}

Synthesize:
1. A 2-3 paragraph document summary
2. Key insights the document reveals (patterns the author may not state explicitly)
3. Which claims are important enough to become permanent knowledge nodes
4. Connections to existing knowledge: {existing_context}

Return ONLY valid JSON:
{{
  "summary": "...",
  "insights": ["insight1", "insight2"],
  "promote_to_matrix": [
    {{"content": "...", "node_type": "FACT|DECISION|INSIGHT", "confidence": 0.8}}
  ],
  "cross_references": [
    {{"source_claim": "...", "related_to": "existing node content", "relationship": "SUPPORTS|CONTRADICTS|RELATES_TO"}}
  ]
}}"""

# ---------------------------------------------------------------------------
# Orchestrator — doc_type → pass plan
# ---------------------------------------------------------------------------

PASS_PLANS: dict[str, list[str]] = {
    "research_paper": ["structure", "topics", "entities", "claims", "synthesis"],
    "report":         ["structure", "topics", "entities", "claims", "synthesis"],
    "legal":          ["structure", "entities", "claims", "synthesis"],
    "interview":      ["structure", "entities", "topics", "synthesis"],
    "notes":          ["structure", "topics", "synthesis"],
    "manual":         ["structure", "topics", "entities", "synthesis"],
    "reference":      ["structure", "entities", "synthesis"],
    "narrative":      ["structure", "topics", "entities", "synthesis"],
}

DEFAULT_PLAN = ["structure", "topics", "entities", "synthesis"]

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PassResult:
    pass_name: str
    output: dict
    duration_ms: float
    token_count: int = 0
    success: bool = True
    error: Optional[str] = None


@dataclass
class CompilationResult:
    doc_type: str
    passes_run: list[str]
    pass_results: dict[str, PassResult]
    total_duration_ms: float
    total_haiku_calls: int
    extractions: list[dict]          # rows for the extractions table
    entity_rows: list[dict]          # rows for the entities table
    matrix_candidates: list[dict]    # nodes to promote to Memory Matrix


# ---------------------------------------------------------------------------
# ForgeCompiler
# ---------------------------------------------------------------------------

# Empty sentinel so _build_prompt can safely access .output
_EMPTY = PassResult(pass_name="", output={}, duration_ms=0)

MAX_INPUT_CHARS = 40_000  # ~10K tokens — Haiku context guard
PASS_TEXT_SLICE = 8_000   # chars of doc text injected into pass 2–5 prompts


class ForgeCompiler:
    """Multi-pass document comprehension using focused Haiku calls."""

    def __init__(self, backend=None):
        if backend is None:
            try:
                from luna.inference.haiku_subtask_backend import HaikuSubtaskBackend
                self._backend = HaikuSubtaskBackend()
            except ImportError:
                self._backend = None
        else:
            self._backend = backend

    @property
    def is_available(self) -> bool:
        return self._backend is not None and self._backend.is_loaded

    # -- public API ----------------------------------------------------------

    async def compile(
        self,
        text: str,
        filename: str = "",
        existing_context: str = "",
        max_tokens_per_pass: int = 1000,
    ) -> CompilationResult:
        """Run multi-pass comprehension on a document."""
        t0 = time.time()
        results: dict[str, PassResult] = {}
        haiku_calls = 0

        doc_text = text[:MAX_INPUT_CHARS]

        # Pass 1: Structure (always first)
        r1 = await self._run_pass("structure", STRUCTURE_PROMPT, doc_text, max_tokens_per_pass)
        results["structure"] = r1
        haiku_calls += 1

        if not r1.success:
            logger.warning("[FORGE-COMPILER] Structure pass failed for %s — using defaults", filename)
            r1 = PassResult(
                pass_name="structure",
                output={"doc_type": "report", "title": filename, "sections": []},
                duration_ms=r1.duration_ms,
                success=True,
            )
            results["structure"] = r1

        doc_type = r1.output.get("doc_type", "report")
        plan = PASS_PLANS.get(doc_type, DEFAULT_PLAN)
        logger.info("[FORGE-COMPILER] %s: doc_type=%s, plan=%s", filename, doc_type, plan)

        # Run remaining passes
        for pass_name in plan:
            if pass_name == "structure":
                continue
            prompt = self._build_prompt(pass_name, doc_text, results, existing_context)
            r = await self._run_pass(pass_name, prompt, "", max_tokens_per_pass)
            results[pass_name] = r
            haiku_calls += 1
            if not r.success:
                logger.warning("[FORGE-COMPILER] Pass %s failed for %s: %s", pass_name, filename, r.error)

        extractions = self._assemble_extractions(results)
        entity_rows = self._assemble_entities(results)
        matrix_candidates = self._assemble_matrix_candidates(results)

        elapsed = (time.time() - t0) * 1000
        logger.info(
            "[FORGE-COMPILER] %s: %d passes, %d extractions, %d entities, %.0fms",
            filename, haiku_calls, len(extractions), len(entity_rows), elapsed,
        )

        return CompilationResult(
            doc_type=doc_type,
            passes_run=[p for p in plan if p in results],
            pass_results=results,
            total_duration_ms=elapsed,
            total_haiku_calls=haiku_calls,
            extractions=extractions,
            entity_rows=entity_rows,
            matrix_candidates=matrix_candidates,
        )

    # -- pass execution ------------------------------------------------------

    async def _run_pass(
        self, pass_name: str, system_prompt: str, user_content: str, max_tokens: int,
    ) -> PassResult:
        t0 = time.time()
        try:
            result = await self._backend.generate(
                user_message=user_content or "Analyze the document.",
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            )
            elapsed = (time.time() - t0) * 1000

            raw = result.text.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            output = json.loads(raw)

            return PassResult(pass_name=pass_name, output=output, duration_ms=elapsed)
        except json.JSONDecodeError as e:
            return PassResult(
                pass_name=pass_name, output={},
                duration_ms=(time.time() - t0) * 1000,
                success=False, error=f"JSON parse failed: {e}",
            )
        except Exception as e:
            return PassResult(
                pass_name=pass_name, output={},
                duration_ms=(time.time() - t0) * 1000,
                success=False, error=str(e),
            )

    # -- prompt assembly -----------------------------------------------------

    def _build_prompt(
        self, pass_name: str, doc_text: str,
        results: dict[str, PassResult], existing_context: str,
    ) -> str:
        structure = json.dumps(results.get("structure", _EMPTY).output, indent=1)
        topics = json.dumps(results.get("topics", _EMPTY).output, indent=1)
        entities = json.dumps(results.get("entities", _EMPTY).output, indent=1)
        claims = json.dumps(results.get("claims", _EMPTY).output, indent=1)

        doc_type = results.get("structure", _EMPTY).output.get("doc_type", "document")
        domain = results.get("topics", _EMPTY).output.get("primary_domain", "general")

        entity_names = ", ".join(
            e.get("name", "")
            for e in results.get("entities", _EMPTY).output.get("entities", [])
        )
        section_summaries = topics  # reuse topics JSON for readability

        text_slice = doc_text[:PASS_TEXT_SLICE]

        prompts = {
            "topics": TOPICS_PROMPT.format(
                structure_json=structure, doc_text=text_slice,
            ),
            "entities": ENTITIES_PROMPT.format(
                primary_domain=domain, structure_json=structure,
                topics_json=topics, doc_text=text_slice,
            ),
            "claims": CLAIMS_PROMPT.format(
                doc_type=doc_type, primary_domain=domain,
                entity_names=entity_names, section_summaries=section_summaries,
                doc_text=text_slice,
            ),
            "synthesis": SYNTHESIS_PROMPT.format(
                doc_type=doc_type, primary_domain=domain,
                structure_json=structure, topics_json=topics,
                entities_json=entities, claims_json=claims,
                existing_context=existing_context[:2000] if existing_context else "none",
            ),
        }
        return prompts.get(pass_name, "")

    # -- extraction assembly -------------------------------------------------

    def _assemble_extractions(self, results: dict[str, PassResult]) -> list[dict]:
        """Convert pass outputs into extraction rows for the DB."""
        extractions: list[dict] = []

        # Structure → TOC
        struct = results.get("structure")
        if struct and struct.success and struct.output.get("sections"):
            toc_lines = []
            for sec in struct.output["sections"]:
                toc_lines.append(sec.get("heading", ""))
                for sub in sec.get("subsections", []):
                    toc_lines.append(f"  {sub.get('heading', '')}")
            extractions.append({
                "node_type": "TABLE_OF_CONTENTS",
                "content": "Document Structure:\n" + "\n".join(toc_lines),
                "confidence": 0.95,
            })

        # Topics → section summaries
        topics = results.get("topics")
        if topics and topics.success:
            for sec in topics.output.get("sections", []):
                summary = sec.get("summary", "")
                if summary:
                    heading = sec.get("heading", "")
                    extractions.append({
                        "node_type": "SECTION_SUMMARY",
                        "content": f"[{heading}] {summary}" if heading else summary,
                        "confidence": 0.85,
                    })

        # Claims → individual claims
        claims_result = results.get("claims")
        if claims_result and claims_result.success:
            for claim in claims_result.output.get("claims", []):
                text = claim.get("claim", "")
                if text:
                    extractions.append({
                        "node_type": "CLAIM",
                        "content": text,
                        "confidence": claim.get("confidence", 0.8),
                    })

        # Synthesis → summary + insights
        synth = results.get("synthesis")
        if synth and synth.success:
            summary = synth.output.get("summary", "")
            if summary:
                extractions.append({
                    "node_type": "DOCUMENT_SUMMARY",
                    "content": summary,
                    "confidence": 0.9,
                })
            for insight in synth.output.get("insights", []):
                extractions.append({
                    "node_type": "INSIGHT",
                    "content": insight,
                    "confidence": 0.75,
                })

        return extractions

    def _assemble_entities(self, results: dict[str, PassResult]) -> list[dict]:
        """Extract entity rows for the entities table."""
        entities_pass = results.get("entities")
        if not entities_pass or not entities_pass.success:
            return []

        rows = []
        for ent in entities_pass.output.get("entities", []):
            name = ent.get("name", "")
            if not name:
                continue
            desc = ent.get("description", "")
            rows.append({
                "entity_type": ent.get("type", "concept"),
                "entity_value": f"{name}: {desc}" if desc else name,
                "confidence": 0.85,
            })
        return rows

    def _assemble_matrix_candidates(self, results: dict[str, PassResult]) -> list[dict]:
        """Extract Memory Matrix promotion candidates from synthesis pass."""
        synth = results.get("synthesis")
        if not synth or not synth.success:
            return []
        return synth.output.get("promote_to_matrix", [])
