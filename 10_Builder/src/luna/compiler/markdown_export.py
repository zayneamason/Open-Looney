"""
Markdown Archive Export -- Phase 1c (Knowledge Compiler)
========================================================

Exports compiled memory nodes as Markdown files with YAML front matter.
Each node becomes a human-readable document that can be opened, read,
and audited by community members.

Sovereignty means inspectability.

Usage:
    exporter = MarkdownExporter(matrix, entity_index)
    result = await exporter.export_scope("project:my-project", local_dir() / "archive" / "my-project")
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    """Statistics from a markdown export run."""

    files_written: int = 0
    bytes_written: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_written": self.files_written,
            "bytes_written": self.bytes_written,
            "skipped": self.skipped,
            "errors": self.errors[:10],
        }


def _sanitize_filename(name: str) -> str:
    """Replace unsafe characters for filenames."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


class MarkdownExporter:
    """
    Export compiled memory nodes as Markdown files with YAML front matter.

    Supports standard nodes and enriched PERSON_BRIEFING templates.
    """

    def __init__(self, matrix, entity_index=None):
        """
        Args:
            matrix: Matrix actor for querying nodes.
            entity_index: Optional EntityIndex for enriching PERSON_BRIEFING exports.
        """
        self._matrix = matrix
        self._entity_index = entity_index

    # ── Public API ──────────────────────────────────────────────────

    async def export_scope(
        self,
        scope: str,
        output_dir: Path,
    ) -> ExportResult:
        """Export all nodes for a scope to markdown files."""
        result = ExportResult()

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            db = self._matrix._matrix.db
            rows = await db.execute_fetchall(
                "SELECT id, content, node_type, tags, confidence, scope, created_at "
                "FROM memory_nodes WHERE scope = ?",
                (scope,),
            )
        except Exception as e:
            result.errors.append(f"Query failed: {e}")
            return result

        if not rows:
            logger.info(f"MarkdownExporter: no nodes found for scope '{scope}'")
            return result

        for row in rows:
            try:
                node = self._row_to_dict(row)
                path = await self._write_node(node, output_dir, scope)
                if path:
                    size = path.stat().st_size
                    result.files_written += 1
                    result.bytes_written += size
                else:
                    result.skipped += 1
            except Exception as e:
                result.errors.append(f"Node {row[0]}: {e}")

        logger.info(
            f"MarkdownExporter: {result.files_written} files, "
            f"{result.bytes_written} bytes to {output_dir}"
        )
        return result

    async def export_single(
        self,
        node_id: str,
        output_dir: Path,
    ) -> Optional[Path]:
        """Export a single node by ID."""
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            db = self._matrix._matrix.db
            rows = await db.execute_fetchall(
                "SELECT id, content, node_type, tags, confidence, scope, created_at "
                "FROM memory_nodes WHERE id = ?",
                (node_id,),
            )
        except Exception:
            return None

        if not rows:
            return None

        node = self._row_to_dict(rows[0])
        return await self._write_node(node, output_dir, node.get("scope", ""))

    # ── Internal ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convert a database row to a node dict."""
        tags_raw = row[3] if row[3] else "[]"
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        except (json.JSONDecodeError, TypeError):
            tags = []

        return {
            "id": row[0],
            "content": row[1] or "",
            "node_type": row[2] or "FACT",
            "tags": tags if isinstance(tags, list) else [],
            "confidence": row[4] if row[4] is not None else 0,
            "scope": row[5] or "",
            "created_at": row[6] or "",
        }

    async def _write_node(
        self, node: dict, output_dir: Path, scope: str
    ) -> Optional[Path]:
        """Write a single node to a markdown file."""
        node_type = node["node_type"]
        node_id = node["id"]
        content = node["content"]

        if not content.strip():
            return None

        # Choose template
        if node_type == "PERSON_BRIEFING":
            md = self._render_person_briefing(node, scope)
        else:
            md = self._render_node(node, scope)

        # Build filename
        short_id = _sanitize_filename(node_id[-12:] if len(node_id) > 12 else node_id)
        filename = f"{node_type}_{short_id}.md"
        path = output_dir / filename

        path.write_text(md, encoding="utf-8")
        return path

    def _render_node(self, node: dict, scope: str) -> str:
        """Render a standard node to markdown with YAML front matter."""
        tags = node["tags"]
        node_type = node["node_type"]
        content = node["content"]
        confidence = node["confidence"]

        # Extract entity references from tags
        entities = [t for t in tags if t not in (
            "guardian", "knowledge", "compiled", "conversation",
            "timeline", "fact", "decision", "action", "insight",
            "problem", "milestone", "quote", "thread_arc",
            "briefing", "constellation", "project_status", "governance",
            node["id"],
        ) and not t.startswith("kn-") and not t.startswith("evt-")]

        # Extract date from content if available
        date_match = re.search(r"Date:\s*(\S+)", content)
        when = date_match.group(1) if date_match else node.get("created_at", "")[:10]

        # Build YAML front matter
        lines = ["---"]
        lines.append(f"node_id: {node['id']}")
        if entities:
            lines.append(f"who: [{', '.join(entities[:5])}]")
        lines.append(f"what: {node_type}")
        if when:
            lines.append(f"when: {when}")
        if scope:
            lines.append(f"where: {scope}")
        lines.append(f"confidence: {confidence / 100 if confidence > 1 else confidence}")
        lines.append("---")
        lines.append("")

        # Content body
        # Use first line as heading if it looks like a title
        content_lines = content.strip().split("\n")
        first_line = content_lines[0].rstrip(".")
        if len(first_line) < 100 and not first_line.startswith("-"):
            lines.append(f"# {first_line}")
            lines.append("")
            lines.extend(content_lines[1:])
        else:
            lines.append(f"# {node_type} Node")
            lines.append("")
            lines.extend(content_lines)

        return "\n".join(lines) + "\n"

    def _render_person_briefing(self, node: dict, scope: str) -> str:
        """Render PERSON_BRIEFING with enriched template."""
        content = node["content"]
        tags = node["tags"]
        confidence = node["confidence"]

        # Try to extract entity ID from tags
        entity_id = None
        for t in tags:
            if t not in ("briefing", "compiled", "constellation") and not t.startswith("kn-"):
                entity_id = t
                break

        # Build YAML
        lines = ["---"]
        lines.append(f"node_id: {node['id']}")
        if entity_id:
            lines.append(f"who: {entity_id}")
        lines.append("what: PERSON_BRIEFING")
        lines.append(f"confidence: {confidence / 100 if confidence > 1 else confidence}")
        lines.append("---")
        lines.append("")

        # Parse content into structured sections
        content_lines = content.strip().split("\n")

        # First line is usually the identity line
        if content_lines:
            name_line = content_lines[0].strip()
            lines.append(f"# {name_line}")
            lines.append("")

        # Look for role/clan info from entity index
        if self._entity_index and entity_id:
            profile = self._entity_index.get_profile(entity_id)
            if profile:
                meta_parts = []
                if profile.role:
                    meta_parts.append(f"**Role:** {profile.role}")
                if profile.clan:
                    meta_parts.append(f"**Clan:** {profile.clan}")
                if profile.location:
                    meta_parts.append(f"**Location:** {profile.location}")
                if meta_parts:
                    lines.append(" | ".join(meta_parts))
                    lines.append("")

        # Render remaining content preserving section headers
        in_section = False
        for line in content_lines[1:]:
            stripped = line.strip()
            if stripped.startswith("Key accomplishments") or stripped.startswith("Key decisions"):
                lines.append(f"## {stripped.rstrip(':')}")
                in_section = True
            elif stripped.startswith("Active/pending"):
                lines.append(f"## {stripped.rstrip(':')}")
                in_section = True
            elif stripped.startswith("Insights"):
                lines.append(f"## {stripped.rstrip(':')}")
                in_section = True
            elif stripped.startswith("Household"):
                lines.append(f"## Key Relationships")
                in_section = True
            elif stripped.startswith("Scope"):
                lines.append(f"\n{stripped}")
                in_section = False
            elif stripped:
                lines.append(stripped)

        return "\n".join(lines) + "\n"
