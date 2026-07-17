"""
PDF Parser
==========

Converts a PDF file into a flat list of document nodes.
Uses pymupdf (fitz) for text extraction, table detection, and image rendering.
Optional OCR for scanned pages via pytesseract.
"""

from __future__ import annotations

import logging
import re
import statistics
from pathlib import Path

from .base import BaseParser

logger = logging.getLogger(__name__)

# Sentence boundary — same regex as markdown parser
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

# Minimum text length to consider a page text-bearing (not scanned)
_MIN_TEXT_CHARS = 50

# Heading detection: font size >= this multiplier of median → heading
_HEADING_SIZE_RATIO = 1.3

# Paragraph gap: vertical distance > this multiplier of line height → new paragraph
_PARA_GAP_RATIO = 1.5


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


def _try_ocr(pixmap) -> str | None:
    """Attempt OCR on a pixmap. Returns text or None if pytesseract unavailable."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("[PDF] pytesseract/Pillow not installed — OCR unavailable")
        return None

    img = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return pytesseract.image_to_string(img)


def _table_bbox_set(page) -> list[tuple[float, float, float, float]]:
    """Return bounding boxes of all tables found on the page."""
    try:
        tables = page.find_tables()
        return [t.bbox for t in tables.tables] if tables and tables.tables else []
    except Exception:
        return []


def _block_in_table(block_bbox: tuple, table_bboxes: list[tuple], tolerance: float = 5.0) -> bool:
    """Check if a text block overlaps with any table region."""
    bx0, by0, bx1, by1 = block_bbox
    for tx0, ty0, tx1, ty1 in table_bboxes:
        # Block is inside table if it overlaps substantially
        if bx0 >= tx0 - tolerance and by0 >= ty0 - tolerance and bx1 <= tx1 + tolerance and by1 <= ty1 + tolerance:
            return True
    return False


def _compute_median_font_size(blocks: list[dict]) -> float:
    """Compute median font size across all text spans on a page."""
    sizes = []
    for block in blocks:
        if block.get("type") != 0:  # type 0 = text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sizes.append(span.get("size", 12.0))
    return statistics.median(sizes) if sizes else 12.0


class PDFParser(BaseParser):
    """Parse PDF files into a flat node list."""

    def parse(self, source_path: Path) -> list[dict]:
        source_path = Path(source_path)

        try:
            import fitz  # pymupdf
        except ImportError:
            raise ImportError(
                "pymupdf is required for PDF parsing. Install with: pip install pymupdf"
            )

        doc = fitz.open(str(source_path))

        nodes: list[dict] = []
        sibling_count: dict[int, int] = {}

        # Root document node
        pdf_title = doc.metadata.get("title", "").strip() if doc.metadata else ""
        title = pdf_title or source_path.stem
        nodes.append(_make_node("document", None, None, 0, {"title": title}))
        root_idx = 0
        sibling_count[root_idx] = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            display_page = page_num + 1  # 1-indexed for humans

            # Page section node
            page_pos = sibling_count[root_idx]
            sibling_count[root_idx] += 1
            page_section_idx = len(nodes)
            nodes.append(_make_node(
                "section", None, root_idx, page_pos,
                {"page_num": display_page},
            ))
            sibling_count[page_section_idx] = 0

            # Detect text vs scanned
            raw_text = page.get_text("text")
            if len(raw_text.strip()) < _MIN_TEXT_CHARS:
                self._parse_scanned_page(nodes, sibling_count, page, page_section_idx, display_page)
            else:
                self._parse_text_page(nodes, sibling_count, page, page_section_idx, display_page)

        doc.close()
        return nodes

    def _parse_text_page(
        self,
        nodes: list[dict],
        sibling_count: dict[int, int],
        page,
        page_section_idx: int,
        page_num: int,
    ) -> None:
        """Extract structured content from a text-bearing PDF page."""
        # Get table bounding boxes to exclude from text extraction
        table_bboxes = _table_bbox_set(page)

        # Extract text blocks with font metadata
        page_dict = page.get_text("dict")
        blocks = page_dict.get("blocks", [])

        # Compute median font size for heading detection
        median_size = _compute_median_font_size(blocks)
        heading_threshold = median_size * _HEADING_SIZE_RATIO

        # Current parent for non-heading content
        current_parent_idx = page_section_idx

        # Process text blocks
        # Collect non-table text blocks with their positions
        text_groups: list[dict] = []  # {text, y0, y1, is_heading, font_size}

        for block in blocks:
            if block.get("type") != 0:  # skip image blocks
                continue

            block_bbox = (block["bbox"][0], block["bbox"][1], block["bbox"][2], block["bbox"][3])

            # Skip blocks inside tables — they'll be handled separately
            if _block_in_table(block_bbox, table_bboxes):
                continue

            for line in block.get("lines", []):
                line_text_parts = []
                max_font_size = 0.0

                for span in line.get("spans", []):
                    span_text = span.get("text", "").strip()
                    if span_text:
                        line_text_parts.append(span_text)
                        max_font_size = max(max_font_size, span.get("size", 12.0))

                line_text = " ".join(line_text_parts).strip()
                if not line_text:
                    continue

                is_heading = max_font_size >= heading_threshold and len(line_text) < 200

                text_groups.append({
                    "text": line_text,
                    "y0": line["bbox"][1],
                    "y1": line["bbox"][3],
                    "is_heading": is_heading,
                    "font_size": max_font_size,
                })

        # Estimate line height from text groups
        line_heights = [g["y1"] - g["y0"] for g in text_groups if g["y1"] > g["y0"]]
        avg_line_height = statistics.mean(line_heights) if line_heights else 14.0
        para_gap = avg_line_height * _PARA_GAP_RATIO

        # Group into paragraphs based on vertical gaps and headings
        paragraphs: list[dict] = []  # {lines: [str], is_heading: bool}
        i = 0

        while i < len(text_groups):
            group = text_groups[i]

            if group["is_heading"]:
                # Heading gets its own entry
                paragraphs.append({"lines": [group["text"]], "is_heading": True})
                i += 1
                continue

            # Start a paragraph
            para_lines = [group["text"]]
            prev_y1 = group["y1"]
            i += 1

            while i < len(text_groups):
                next_group = text_groups[i]
                if next_group["is_heading"]:
                    break
                gap = next_group["y0"] - prev_y1
                if gap > para_gap:
                    break
                para_lines.append(next_group["text"])
                prev_y1 = next_group["y1"]
                i += 1

            paragraphs.append({"lines": para_lines, "is_heading": False})

        # Emit nodes for paragraphs and headings
        for para in paragraphs:
            if para["is_heading"]:
                heading_text = para["lines"][0]
                pos = sibling_count[page_section_idx]
                sibling_count[page_section_idx] += 1
                heading_idx = len(nodes)
                nodes.append(_make_node(
                    "section", heading_text, page_section_idx, pos,
                    {"page_num": page_num, "title": heading_text},
                ))
                sibling_count[heading_idx] = 0
                current_parent_idx = heading_idx
            else:
                para_text = " ".join(para["lines"]).strip()
                if not para_text:
                    continue

                parent = current_parent_idx
                pos = sibling_count[parent]
                sibling_count[parent] += 1
                para_idx = len(nodes)
                nodes.append(_make_node("paragraph", None, parent, pos, {"page_num": page_num}))
                sibling_count[para_idx] = 0

                sentences = _split_sentences(para_text)
                if not sentences:
                    sentences = [para_text]

                for s_i, sentence in enumerate(sentences):
                    nodes.append(_make_node(
                        "sentence", sentence, para_idx, s_i,
                        {"page_num": page_num},
                    ))

        # Process tables
        try:
            tables = page.find_tables()
            if tables and tables.tables:
                for table in tables.tables:
                    parent = current_parent_idx
                    pos = sibling_count[parent]
                    sibling_count[parent] += 1
                    table_idx = len(nodes)
                    nodes.append(_make_node("table", None, parent, pos, {"page_num": page_num}))
                    sibling_count[table_idx] = 0

                    rows = table.extract()
                    for row_i, row_data in enumerate(rows):
                        is_header = row_i == 0
                        row_idx = len(nodes)
                        row_meta = {"header": True, "page_num": page_num} if is_header else {"page_num": page_num}
                        nodes.append(_make_node("row", None, table_idx, row_i, row_meta))

                        for col_i, cell_text in enumerate(row_data):
                            cell_content = (cell_text or "").strip()
                            nodes.append(_make_node(
                                "cell", cell_content, row_idx, col_i,
                                {"column": col_i, "page_num": page_num},
                            ))
        except Exception as e:
            logger.warning("[PDF] Table extraction failed on page %d: %s", page_num, e)

    def _parse_scanned_page(
        self,
        nodes: list[dict],
        sibling_count: dict[int, int],
        page,
        page_section_idx: int,
        page_num: int,
    ) -> None:
        """Extract content from a scanned page via OCR."""
        pixmap = page.get_pixmap(dpi=300)
        ocr_text = _try_ocr(pixmap)

        if ocr_text is None:
            # pytesseract not available
            pos = sibling_count[page_section_idx]
            sibling_count[page_section_idx] += 1
            para_idx = len(nodes)
            nodes.append(_make_node(
                "paragraph", None, page_section_idx, pos,
                {"ocr_skipped": True, "page_num": page_num},
            ))
            sibling_count[para_idx] = 0
            nodes.append(_make_node(
                "sentence", "[Scanned page — OCR not available]", para_idx, 0,
                {"ocr_skipped": True, "page_num": page_num},
            ))
            return

        # Split OCR text into paragraphs by double newlines
        raw_paragraphs = re.split(r"\n{2,}", ocr_text.strip())

        for p_i, para_text in enumerate(raw_paragraphs):
            para_text = " ".join(para_text.split()).strip()  # normalize whitespace
            if not para_text:
                continue

            pos = sibling_count[page_section_idx]
            sibling_count[page_section_idx] += 1
            para_idx = len(nodes)
            nodes.append(_make_node(
                "paragraph", None, page_section_idx, pos,
                {"ocr": True, "page_num": page_num},
            ))
            sibling_count[para_idx] = 0

            sentences = _split_sentences(para_text)
            if not sentences:
                sentences = [para_text]

            for s_i, sentence in enumerate(sentences):
                nodes.append(_make_node(
                    "sentence", sentence, para_idx, s_i,
                    {"ocr": True, "page_num": page_num},
                ))
