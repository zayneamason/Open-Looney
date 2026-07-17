"""
Markdown Parser
===============

Converts a markdown file into a flat list of document nodes.
Regex-based — no nltk or heavy NLP dependencies.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .base import BaseParser

# Sentence boundary: period/exclamation/question + whitespace + capital letter
# Same regex as luna.substrate.aibrarian_engine.SENTENCE_BOUNDARY
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

# Markdown patterns
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE = re.compile(r"^```(\w*)$")
_TABLE_ROW = re.compile(r"^\|(.+)\|$")
_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.+)$")
_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
_BLOCKQUOTE = re.compile(r"^>\s?(.*)")


def _make_node(
    type: str,
    content: str | None,
    parent_idx: int | None,
    position: int,
    meta: dict | None = None,
) -> dict:
    return {
        "type": type,
        "content": content,
        "parent_idx": parent_idx,
        "position": position,
        "meta": meta,
    }


def _split_sentences(text: str) -> list[str]:
    """Split paragraph text into sentences using a simple regex."""
    parts = _SENTENCE_BOUNDARY.split(text.strip())
    return [s.strip() for s in parts if s.strip()]


class MarkdownParser(BaseParser):
    """Parse markdown files into a flat node list."""

    def parse(self, source_path: Path) -> list[dict]:
        source_path = Path(source_path)
        text = source_path.read_text(encoding="utf-8", errors="replace")
        return self._parse_text(text, source_path.stem)

    def _parse_text(self, text: str, title: str = "") -> list[dict]:
        nodes: list[dict] = []
        sibling_count: Counter = Counter()

        # Root document node (index 0)
        nodes.append(_make_node("document", None, None, 0, {"title": title}))
        root_idx = 0

        # Section stack: list of (node_index, heading_level)
        section_stack: list[tuple[int, int]] = []

        lines = text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # --- Fenced code block ---
            fence_match = _FENCE.match(line)
            if fence_match:
                lang = fence_match.group(1) or None
                code_lines = []
                i += 1
                while i < len(lines) and not _FENCE.match(lines[i]):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing fence

                parent = section_stack[-1][0] if section_stack else root_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1
                meta = {"language": lang} if lang else None
                nodes.append(_make_node("paragraph", "\n".join(code_lines), parent, pos, meta))
                continue

            # --- Heading ---
            heading_match = _HEADING.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()

                # Pop sections at same or deeper level
                while section_stack and section_stack[-1][1] >= level:
                    section_stack.pop()

                parent = section_stack[-1][0] if section_stack else root_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1

                section_idx = len(nodes)
                nodes.append(_make_node("section", heading_text, parent, pos, {"level": level}))
                section_stack.append((section_idx, level))
                i += 1
                continue

            # --- Image ---
            img_match = _IMAGE.match(line.strip())
            if img_match:
                alt = img_match.group(1)
                src = img_match.group(2)
                parent = section_stack[-1][0] if section_stack else root_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1
                nodes.append(_make_node("figure", alt or None, parent, pos, {"src": src}))
                i += 1
                continue

            # --- Table ---
            if _TABLE_ROW.match(line.strip()) and not _TABLE_SEP.match(line.strip()):
                table_lines = []
                while i < len(lines) and (_TABLE_ROW.match(lines[i].strip()) or _TABLE_SEP.match(lines[i].strip())):
                    table_lines.append(lines[i].strip())
                    i += 1

                parent = section_stack[-1][0] if section_stack else root_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1
                table_idx = len(nodes)
                nodes.append(_make_node("table", None, parent, pos))

                row_pos = 0
                for tl in table_lines:
                    if _TABLE_SEP.match(tl):
                        continue  # skip separator row
                    cells = [c.strip() for c in tl.strip("|").split("|")]
                    is_header = row_pos == 0
                    row_idx = len(nodes)
                    row_meta = {"header": True} if is_header else None
                    nodes.append(_make_node("row", None, table_idx, row_pos, row_meta))

                    for col_i, cell_text in enumerate(cells):
                        nodes.append(_make_node("cell", cell_text, row_idx, col_i, {"column": col_i}))

                    row_pos += 1
                continue

            # --- List ---
            list_match = _LIST_ITEM.match(line)
            if list_match:
                parent = section_stack[-1][0] if section_stack else root_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1
                list_idx = len(nodes)
                nodes.append(_make_node("list", None, parent, pos))

                item_pos = 0
                while i < len(lines) and _LIST_ITEM.match(lines[i]):
                    m = _LIST_ITEM.match(lines[i])
                    item_text = m.group(3).strip()
                    nodes.append(_make_node("list_item", item_text, list_idx, item_pos))
                    item_pos += 1
                    i += 1
                continue

            # --- Blockquote ---
            bq_match = _BLOCKQUOTE.match(line)
            if bq_match:
                bq_lines = []
                while i < len(lines) and _BLOCKQUOTE.match(lines[i]):
                    bq_lines.append(_BLOCKQUOTE.match(lines[i]).group(1))
                    i += 1
                bq_text = " ".join(bq_lines).strip()
                if bq_text:
                    parent = section_stack[-1][0] if section_stack else root_idx
                    pos = sibling_count[parent]
                    sibling_count[parent] += 1
                    nodes.append(_make_node("paragraph", bq_text, parent, pos, {"blockquote": True}))
                continue

            # --- Blank line ---
            if not line.strip():
                i += 1
                continue

            # --- Paragraph (default) ---
            para_lines = []
            while i < len(lines):
                ln = lines[i]
                if not ln.strip():
                    break
                if _HEADING.match(ln) or _FENCE.match(ln) or _TABLE_ROW.match(ln.strip()):
                    break
                if _LIST_ITEM.match(ln) or _IMAGE.match(ln.strip()):
                    break
                if _BLOCKQUOTE.match(ln):
                    break
                para_lines.append(ln)
                i += 1

            para_text = " ".join(para_lines).strip()
            if not para_text:
                i += 1
                continue

            parent = section_stack[-1][0] if section_stack else root_idx
            pos = sibling_count[parent]
            sibling_count[parent] += 1
            para_idx = len(nodes)
            nodes.append(_make_node("paragraph", None, parent, pos))

            # Split into sentence children
            sentences = _split_sentences(para_text)
            if not sentences:
                sentences = [para_text]

            for s_i, sentence in enumerate(sentences):
                nodes.append(_make_node("sentence", sentence, para_idx, s_i))

            i += 1 if not para_lines else 0  # avoid double-increment

        return nodes
