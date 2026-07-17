"""
Cartridge Builder
=================

Orchestrator: source file → .lun SQLite cartridge.

    Usage:
    python -m luna.cartridge.builder input.md [output.lun]
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .schema import LUN_SCHEMA

logger = logging.getLogger(__name__)


# SPEC-006: title validation — reject parser artifacts and placeholders.
PARSER_ARTIFACT_PATTERN = re.compile(r"^[/.\\\-_\s]{1,3}\s")
TITLE_PLACEHOLDER_SET = {"untitled", "document", "document1"}


def _validate_title(raw_title: str, source_filename: str) -> tuple[str, bool]:
    """Validate parsed title; return (final_title, used_fallback).
    Falls back to filename stem if title is rejected.
    Whitespace normalization catches 'Document 1', 'DOCUMENT_1', ' untitled '."""
    title = (raw_title or "").strip()
    fallback = Path(source_filename).stem

    if len(title) < 3:
        return fallback, True
    if PARSER_ARTIFACT_PATTERN.match(title):
        return fallback, True
    if not re.search(r"[A-Za-z0-9]", title):
        return fallback, True
    normalized = "".join(title.casefold().split())
    if normalized in TITLE_PLACEHOLDER_SET:
        return fallback, True
    return title, False


def finalize_for_shipping(conn: sqlite3.Connection) -> None:
    """SPEC-006: shipping-mode finalization. Must run AFTER all writes are
    committed and BEFORE conn.close(). VACUUM cannot run inside a transaction,
    so the caller must commit first. journal_mode=DELETE before VACUUM —
    VACUUM in WAL leaves a -wal sidecar behind."""
    conn.execute("PRAGMA optimize")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.execute("VACUUM")


class ULIDGenerator:
    """SPEC-002 D1: canonical monotonic ULID generator.

    Each call to next() returns a strictly increasing 26-char Crockford Base32
    string in canonical form: 48 bits timestamp (chars 0-9) + 80 bits randomness
    (chars 10-25), 128 bits total. The top 2 bits of the first 5-bit char are
    always zero, so the first character is restricted to [0-7] per the ULID spec.

    Monotonicity within a single millisecond is achieved by incrementing the
    80-bit random portion by 1; if it overflows (~10^24 ULIDs/ms — astronomically
    rare), the timestamp is bumped by 1 ms and the random portion is re-seeded
    from os.urandom.

    Generator state is per-instance — create one generator per cartridge build."""

    CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

    def __init__(self) -> None:
        self._last_ts: int = 0
        self._last_rand: int = 0  # 80-bit value; populated on first next()

    def next(self) -> str:
        ts = int(time.time() * 1000)
        if ts <= self._last_ts:
            # Same or earlier ms — preserve monotonicity by incrementing random.
            ts = self._last_ts
            self._last_rand += 1
            if self._last_rand >= (1 << 80):
                # Random overflowed 80 bits within the same ms — bump ts and re-seed.
                ts = self._last_ts = ts + 1
                self._last_rand = int.from_bytes(os.urandom(10), "big")
        else:
            # New ms — fresh random.
            self._last_ts = ts
            self._last_rand = int.from_bytes(os.urandom(10), "big")

        # Canonical layout: 48 bits ts + 80 bits rand = 128 bits.
        # 26 chars × 5 bits = 130 bits → top 2 bits of first char are always zero.
        val = (ts << 80) | self._last_rand

        chars = [""] * 26
        for i in range(25, -1, -1):
            chars[i] = self.CROCKFORD[val & 0x1F]
            val >>= 5
        return "".join(chars)


# Phase 5 Step 5: validators centralized in luna.cartridge.validation.
# Re-exported here for public API preservation.
from .validation import (  # noqa: E402,F401
    ULID_RE,
    BuildError,
    validate_anchors,
    validate_extractions,
    validate_ulids,
)


class CartridgeBuilder:
    """Build .lun Knowledge Cartridges from source documents."""

    def __init__(
        self,
        output_dir: Path | str | None = None,
        extract: bool = True,
        embed: bool = True,
    ):
        self.output_dir = Path(output_dir) if output_dir else None
        self.do_extract = extract
        self.do_embed = embed

    async def build(
        self,
        source_path: Path | str,
        output_path: Path | str | None = None,
        preserve_paths: bool = False,
    ) -> Path:
        """Build a .lun cartridge from a source file.

        Args:
            source_path: Path to the source document (.md, .pdf, .csv, .xlsx)
            output_path: Optional explicit output path. If None, derived from source.
            preserve_paths: If True, write the absolute source path into meta as
                source_canonical_path. Default False (privacy/portability).

        Returns:
            Path to the generated .lun file.
        """
        source_path = Path(source_path)

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        ext = source_path.suffix.lower()
        supported_exts = (".md", ".markdown", ".pdf", ".csv", ".xlsx")
        if ext not in supported_exts:
            raise ValueError(
                f"Unsupported format: {ext}. Supported: .md, .markdown, .pdf, .csv, .xlsx"
            )

        # Determine output path
        if output_path:
            lun_path = Path(output_path)
        elif self.output_dir:
            lun_path = self.output_dir / f"{source_path.stem}.lun"
        else:
            lun_path = source_path.with_suffix(".lun")

        lun_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing cartridge (rebuild)
        if lun_path.exists():
            lun_path.unlink()

        logger.info("[CARTRIDGE] Building %s → %s", source_path.name, lun_path.name)

        # Parse
        if ext == ".pdf":
            from .parsers.pdf import PDFParser
            parser = PDFParser()
        elif ext == ".csv":
            from .parsers.csv import CSVParser
            parser = CSVParser()
        elif ext == ".xlsx":
            from .parsers.spreadsheet import SpreadsheetParser
            parser = SpreadsheetParser()
        else:
            from .parsers.markdown import MarkdownParser
            parser = MarkdownParser()
        nodes = parser.parse(source_path)
        logger.info("[CARTRIDGE] Parsed %d nodes from %s", len(nodes), source_path.name)

        # Create database
        conn = sqlite3.connect(str(lun_path))
        # SPEC-006: cartridge family identity (LUNC = Luna Cartridge) and v0.2 baseline.
        # MUST be set before executescript so file identity is established before DDL.
        conn.execute("PRAGMA application_id = 0x4C554E43")
        conn.execute("PRAGMA user_version = 2")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(LUN_SCHEMA)

        # Insert nodes — remap parent_idx to parent_id
        idx_to_db_id: dict[int, int] = {}

        # SPEC-002: per-build ULID generator + id→ulid map for downstream lookups.
        ulid_gen = ULIDGenerator()
        node_id_to_ulid: dict[int, str] = {}

        for idx, node in enumerate(nodes):
            parent_idx = node.get("parent_idx")
            parent_id = idx_to_db_id.get(parent_idx) if parent_idx is not None else None
            meta_json = json.dumps(node.get("meta")) if node.get("meta") else None
            node_ulid = ulid_gen.next()

            cursor = conn.execute(
                "INSERT INTO doc_nodes (parent_id, type, position, content, meta_json, ulid) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (parent_id, node["type"], node["position"], node.get("content"), meta_json, node_ulid),
            )
            db_id = cursor.lastrowid
            idx_to_db_id[idx] = db_id
            node_id_to_ulid[db_id] = node_ulid

        # Write meta
        source_bytes = source_path.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()

        # Extract title from first section or filename, then validate.
        raw_title = source_path.stem
        for node in nodes:
            if node["type"] == "section" and node.get("content"):
                raw_title = node["content"]
                break
        title, used_fallback = _validate_title(raw_title, source_path.name)
        if used_fallback:
            logger.warning(
                "[CARTRIDGE] Title rejected (parser artifact or placeholder): %r → using fallback %r",
                raw_title, title,
            )

        word_count = sum(
            len(n.get("content", "").split())
            for n in nodes
            if n.get("content")
        )

        source_format = {
            ".pdf": "pdf",
            ".csv": "csv",
            ".xlsx": "xlsx",
        }.get(ext, "markdown")

        meta_entries = {
            "title": title,
            # SPEC-006: cartridge family + v0.2 identity
            "format_version": "0.2",
            "cartridge_kind": "knowledge",
            "source_filename": source_path.name,
            "source_format": source_format,
            "source_hash": source_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "word_count": str(word_count),
            "node_count": str(len(nodes)),
            "embedding_model": "all-MiniLM-L6-v2",
            "embedding_dim": "384",
            # SPEC-002 Q3: forward warning to external tools that these integer
            # columns are deprecated. v0.3 removal-phase spec will drop them.
            "deprecated_columns": "doc_nodes.id,extractions.id",
            # SPEC-003: v0.2 logprob attribution contract — natural log, response-level.
            "logprob_base": "e",
            "logprob_attribution": "response_level",
        }
        if preserve_paths:
            meta_entries["source_canonical_path"] = str(source_path.resolve())

        for key, value in meta_entries.items():
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )

        conn.commit()

        # SPEC-002: bundle generator + id→ulid map for extractor/embedder.
        ulid_ctx = {"gen": ulid_gen, "node_id_to_ulid": node_id_to_ulid}

        # Extraction pass
        if self.do_extract:
            try:
                from .extractor import CartridgeExtractor
                extractor = CartridgeExtractor()
                if extractor.is_available:
                    count = await extractor.extract(conn, ulid_ctx=ulid_ctx)
                    logger.info("[CARTRIDGE] Extraction: %d artifacts", count)
                else:
                    logger.warning("[CARTRIDGE] Haiku not available — skipping extraction")
            except Exception as e:
                logger.warning("[CARTRIDGE] Extraction failed (non-fatal): %s", e)

        # Embedding pass
        if self.do_embed:
            try:
                from .embedder import CartridgeEmbedder
                embedder = CartridgeEmbedder()
                count = await embedder.embed(conn, ulid_ctx=ulid_ctx)
                logger.info("[CARTRIDGE] Embeddings: %d vectors", count)
            except Exception as e:
                logger.warning("[CARTRIDGE] Embedding failed (non-fatal): %s", e)

        # SPEC-003: signal contract (cheapest, structural) first. Failure here
        # short-circuits before identity/semantic validation runs.
        validate_extractions(conn)

        # SPEC-002: validate ULID population, format, uniqueness, and cross-ref
        # integrity. Runs before validate_anchors() so structural ULID problems
        # surface before semantic anchor ones.
        validate_ulids(conn)

        # SPEC-001: validate anchor classification invariants before shipping.
        # Raises BuildError if any claim is still 'unknown' or an invariant is violated.
        validate_anchors(conn)

        # SPEC-006: flush pending writes from extractor/embedder, then run
        # shipping-mode pragmas (VACUUM cannot run inside a transaction).
        conn.commit()
        finalize_for_shipping(conn)

        conn.close()
        logger.info("[CARTRIDGE] Built %s (%d nodes, %d words)", lun_path.name, len(nodes), word_count)
        return lun_path


# ---------------------------------------------------------------------------
# CLI entry point: python -m luna.cartridge.builder input.md [output.lun]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m luna.cartridge.builder <input.md|input.pdf|input.csv|input.xlsx> [output.lun] [--preserve-paths] [--no-extract] [--no-embed]")
        print("       --preserve-paths: include source_canonical_path in meta (off by default; leaks build environment)")
        sys.exit(1)

    source = Path(sys.argv[1])
    output = None
    do_extract = True
    do_embed = True
    preserve_paths = False

    for arg in sys.argv[2:]:
        if arg == "--no-extract":
            do_extract = False
        elif arg == "--no-embed":
            do_embed = False
        elif arg == "--preserve-paths":
            preserve_paths = True
        elif not arg.startswith("--"):
            output = Path(arg)

    builder = CartridgeBuilder(extract=do_extract, embed=do_embed)
    result = asyncio.run(builder.build(source, output, preserve_paths=preserve_paths))
    print(f"Built: {result}")
