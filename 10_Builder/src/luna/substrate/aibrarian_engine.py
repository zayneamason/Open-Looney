"""
AiBrarian Engine — Universal Document Database Layer
=====================================================

Manages multiple document databases through a single interface.
Any Luna surface (MCP, Guardian, companion UI, Sheets, Eclissi)
calls the same methods.

Usage:
    engine = AiBrarianEngine("config/aibrarian_registry.yaml")
    await engine.initialize()

    results = await engine.search("dataroom", "budget solar")
    similar = await engine.similar("dataroom", doc_id, limit=5)
    hits = await engine.co_occurrence("dataroom", ["village", "grant"])
    available = engine.list_collections()
    doc_id = await engine.ingest("dataroom", file_path, metadata={})
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .aibrarian_schema import CARTRIDGE_SCHEMA, EMBEDDING_TABLE_SQL, EXTRACTIONS_FTS_MIGRATION, FORGE_SYNC_SCHEMA, INVESTIGATION_SCHEMA, NEXUS_REFS_SCHEMA, STANDARD_SCHEMA

logger = logging.getLogger(__name__)


def _ei_ie_variants(token: str) -> list[str]:
    """Return edit-distance-1 variants of `token` for the ei↔ie typo class.

    Mirrors the same helper in substrate/memory.py — intentionally duplicated
    so aibrarian_engine stays self-contained.
    """
    variants: list[str] = []
    seen: set[str] = set()
    for i in range(len(token) - 1):
        pair = token[i:i + 2]
        if pair == "ei":
            v = token[:i] + "ie" + token[i + 2:]
        elif pair == "ie":
            v = token[:i] + "ei" + token[i + 2:]
        else:
            continue
        if v != token and v not in seen:
            seen.add(v)
            variants.append(v)
    return variants


# ---------------------------------------------------------------------------
# Document Comprehension Prompt (used by extract() at ingest time)
# ---------------------------------------------------------------------------

DOCUMENT_EXTRACTION_PROMPT = """
You are extracting structured knowledge from a document for a
long-term memory system. Your output will be stored and searched
later when someone asks questions about this document.

Extract the following from the provided text:

1. SUMMARY: A comprehensive summary of what this section covers.
   Write it as if explaining to someone who hasn't read it.
   Include specific names, places, systems, and arguments.
   200-400 words.

2. CLAIMS: Key arguments, assertions, or findings the author makes.
   Each claim should be a standalone sentence that captures a
   specific point. Include 3-10 claims per section.

3. ENTITIES: People, places, systems, organizations, and concepts
   mentioned. For each entity, note its type and a brief
   description of its role in the text.

Return ONLY valid JSON in this exact format:
{
  "summary": "Comprehensive summary of the section...",
  "claims": [
    "Specific claim or argument from the text",
    "Another specific claim or finding"
  ],
  "entities": [
    {"name": "Entity Name", "type": "person|place|system|org|concept",
     "description": "Brief role in the text"}
  ]
}

RULES:
- Write summaries and claims in neutral third person
- Be specific: include names, numbers, dates when present
- Claims must be attributable to the text, not your inference
- If the text is a title page, table of contents, or index,
  return {"summary": "", "claims": [], "entities": []}
- Return ONLY JSON. No markdown, no explanation.
"""


# ---------------------------------------------------------------------------
# Document Chunking (word-based)
# ---------------------------------------------------------------------------

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass
class DocumentChunk:
    """A chunk of a document for indexing and embedding."""

    text: str
    index: int
    start_char: int
    end_char: int
    source_id: str = ""
    word_count: int = 0
    section_label: str = ""
    section_level: int = 0


# ---------------------------------------------------------------------------
# Document Structure Detection
# ---------------------------------------------------------------------------


@dataclass
class StructuralMarker:
    """A detected chapter or section boundary in a document."""

    heading: str  # "Chapter 2: The Powers of Water"
    level: int  # 1 = chapter, 2 = section, 3 = subsection
    start_char: int  # Character offset where this section begins
    end_char: int  # Character offset where next section begins (or end of doc)
    page_hint: str = ""  # Page number if detected (e.g., "37")


# Common heading patterns for structure detection
STRUCTURE_PATTERNS = [
    # "Chapter 1: Title" or "CHAPTER 1" or "Chapter One"
    re.compile(
        r"^(?:CHAPTER|Chapter)\s+(\d+|[A-Z][a-z]+)(?:\s*[:.]\s*(.+))?$",
        re.MULTILINE,
    ),
    # "1. Title" or "I. Title" (numbered sections at line start)
    re.compile(
        r"^(\d+|[IVXLC]+)\.\s+([A-Z][A-Za-z\s:]+)$",
        re.MULTILINE,
    ),
    # "Part I" / "Part 1"
    re.compile(
        r"^(?:PART|Part)\s+(\d+|[IVXLC]+)(?:\s*[:.]\s*(.+))?$",
        re.MULTILINE,
    ),
    # "Introduction" / "Conclusion" / "Preface" / "Epilogue" (standalone)
    re.compile(
        r"^(Introduction|Conclusion|Preface|Epilogue|Foreword|Afterword|Appendix(?:\s+[A-Z])?)(?:\s*[:.]\s*(.+))?$",
        re.MULTILINE,
    ),
    # ALL CAPS heading on its own line (≤ 60 chars, ≥ 5 chars)
    re.compile(
        r"^([A-Z][A-Z\s]{4,60})$",
        re.MULTILINE,
    ),
]

# Page number patterns (pdftotext often inserts these)
PAGE_NUMBER_PATTERN = re.compile(r"^\s*(\d{1,4})\s*$", re.MULTILINE)


def detect_document_structure(text: str) -> list[StructuralMarker]:
    """
    Detect chapter/section structure from document text.

    Scans for heading patterns and returns ordered list of
    structural markers with character offsets. Works best with
    text extracted via pdftotext which preserves line breaks.

    Returns empty list if no structure detected (short docs, etc.)
    """
    if len(text) < 5000:
        return []  # Too short to have meaningful structure

    markers: list[StructuralMarker] = []

    for pattern in STRUCTURE_PATTERNS:
        for match in pattern.finditer(text):
            heading = match.group(0).strip()
            start = match.start()

            # Determine level
            full = heading.lower()
            if full.startswith(("chapter", "part")):
                level = 1
            elif full.startswith((
                "introduction", "conclusion", "preface",
                "epilogue", "foreword", "afterword", "appendix",
            )):
                level = 1
            elif heading.isupper():
                level = 2
            else:
                level = 2

            # Find nearest page number (look backward up to 200 chars)
            page_hint = ""
            lookback = text[max(0, start - 200):start]
            page_matches = list(PAGE_NUMBER_PATTERN.finditer(lookback))
            if page_matches:
                page_hint = page_matches[-1].group(1)

            markers.append(StructuralMarker(
                heading=heading,
                level=level,
                start_char=start,
                end_char=len(text),  # Will be corrected below
                page_hint=page_hint,
            ))

    if not markers:
        return []

    # Sort by position, deduplicate overlapping matches
    markers.sort(key=lambda m: m.start_char)

    # Remove duplicates (overlapping patterns matching same heading)
    deduped: list[StructuralMarker] = []
    for m in markers:
        if deduped and abs(m.start_char - deduped[-1].start_char) < 50:
            # Keep the one with more specific level
            if m.level < deduped[-1].level:
                deduped[-1] = m
            continue
        deduped.append(m)

    # Set end_char to next marker's start
    for i in range(len(deduped) - 1):
        deduped[i].end_char = deduped[i + 1].start_char
    # Last marker extends to end of document (already set)

    return deduped


def chunk_document(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    source_id: str = "",
    preserve_sentences: bool = True,
    structure: list[StructuralMarker] | None = None,
) -> list[DocumentChunk]:
    """
    Split document text into overlapping word-based chunks.

    Preserves sentence boundaries when possible to keep chunks semantically
    coherent.  This is different from the SemanticChunker in extraction/
    which operates on conversation turns with token estimation.

    Args:
        text: Full document text.
        chunk_size: Target words per chunk.
        overlap: Words of overlap between adjacent chunks.
        source_id: Document ID to tag chunks with.
        preserve_sentences: Try to break at sentence boundaries.

    Returns:
        List of DocumentChunk objects.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[DocumentChunk] = []
    start_word = 0
    chunk_index = 0

    while start_word < len(words):
        end_word = min(start_word + chunk_size, len(words))

        # Snap to sentence boundary if possible
        if preserve_sentences and end_word < len(words):
            chunk_text = " ".join(words[start_word:end_word])
            boundaries = list(SENTENCE_BOUNDARY.finditer(chunk_text))
            if boundaries:
                last_boundary = boundaries[-1]
                boundary_pos = last_boundary.start()
                words_to_boundary = len(chunk_text[:boundary_pos].split())
                if words_to_boundary > chunk_size * 0.6:
                    end_word = start_word + words_to_boundary

        chunk_words = words[start_word:end_word]
        chunk_text = " ".join(chunk_words)
        start_char = len(" ".join(words[:start_word])) + (1 if start_word > 0 else 0)
        end_char = start_char + len(chunk_text)

        chunk = DocumentChunk(
            text=chunk_text,
            index=chunk_index,
            start_char=start_char,
            end_char=end_char,
            source_id=source_id,
            word_count=len(chunk_words),
        )

        # Tag chunk with structural section
        if structure:
            for marker in reversed(structure):
                if chunk.start_char >= marker.start_char:
                    chunk.section_label = marker.heading
                    chunk.section_level = marker.level
                    break

        chunks.append(chunk)

        chunk_index += 1
        if end_word >= len(words):
            break
        start_word = end_word - overlap

    # Edge case: empty text that passed the word split
    if not chunks:
        chunks.append(
            DocumentChunk(
                text=text,
                index=0,
                start_char=0,
                end_char=len(text),
                source_id=source_id,
                word_count=len(words),
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# File Content Reading
# ---------------------------------------------------------------------------


def read_file_content(file_path: Path) -> str:
    """
    Read text content from a file, dispatching by extension.

    Supports: .md, .txt, .csv, .json, .yaml/.yml, .pdf (via pdftotext),
    .docx (via pandoc).
    """
    suffix = file_path.suffix.lower()

    if suffix in (".md", ".txt", ".csv", ".json", ".yaml", ".yml"):
        try:
            return file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file_path.read_text(encoding="latin-1")

    if suffix == ".pdf":
        try:
            result = subprocess.run(
                ["pdftotext", str(file_path), "-"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout if result.returncode == 0 else ""
        except FileNotFoundError:
            logger.warning("pdftotext not installed — skipping PDF: %s", file_path.name)
            return ""

    if suffix == ".docx":
        try:
            result = subprocess.run(
                ["pandoc", str(file_path), "-t", "plain"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout if result.returncode == 0 else ""
        except FileNotFoundError:
            logger.warning("pandoc not installed — skipping DOCX: %s", file_path.name)
            return ""

    logger.warning("Unsupported file type: %s", suffix)
    return ""


# ---------------------------------------------------------------------------
# sqlite-vec helpers (match existing embeddings.py patterns)
# ---------------------------------------------------------------------------


def _vector_to_blob(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _blob_to_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ---------------------------------------------------------------------------
# Configuration Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AiBrarianConfig:
    """Configuration for a single AiBrarian collection."""

    key: str
    name: str
    description: str = ""
    db_path: str = ""
    enabled: bool = True
    read_only: bool = False
    create_if_missing: bool = False
    schema_type: str = "standard"
    source_dir: str = ""
    extract_on_ingest: bool = True
    extraction_model: str = "claude-haiku"
    chunk_size: int = 500
    chunk_overlap: int = 50
    embedding_model: str = "local-minilm"
    embedding_dim: int = 384
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    project_key: str = ""
    ingestion_pattern: str = "utilitarian"
    reflection_mode: str = ""                # "precision" | "reflective" | "relational"
    grounding_priority: str = "supplemental" # "primary" | "supplemental" | "background"
    watch: bool = False                      # auto-watch source_dir on startup
    multi_pass_ingest: bool = True           # use ForgeCompiler when available
    bridge_to_matrix: bool = False           # promote entities/insights to Memory Matrix


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AiBrarianRegistry:
    """Loads and manages AiBrarian collection configurations from YAML."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.collections: dict[str, AiBrarianConfig] = {}
        self.defaults: dict = {}

    def load(self) -> None:
        if not self.config_path.exists():
            logger.warning("AiBrarian registry not found: %s", self.config_path)
            return

        with open(self.config_path) as f:
            data = yaml.safe_load(f) or {}

        self.defaults = data.get("defaults", {})

        for key, conf in data.get("collections", {}).items():
            merged = {**self.defaults, **conf}
            # Remove keys that are not AiBrarianConfig fields
            valid_keys = {f.name for f in AiBrarianConfig.__dataclass_fields__.values()}
            filtered = {k: v for k, v in merged.items() if k in valid_keys}
            if "name" not in filtered:
                filtered["name"] = key
            self.collections[key] = AiBrarianConfig(key=key, **filtered)

    def get(self, key: str) -> Optional[AiBrarianConfig]:
        return self.collections.get(key)

    def list_enabled(self) -> list[AiBrarianConfig]:
        return [c for c in self.collections.values() if c.enabled]

    def resolve_db_path(self, config: AiBrarianConfig, project_root: Path) -> Path:
        p = Path(config.db_path)
        return p if p.is_absolute() else project_root / p


# ---------------------------------------------------------------------------
# Per-Collection Connection
# ---------------------------------------------------------------------------


class AiBrarianConnection:
    """Manages a single AiBrarian collection SQLite database connection."""

    def __init__(self, config: AiBrarianConfig, db_path: Path):
        self.config = config
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._vec_loaded = False

    async def connect(self) -> None:
        if not self.db_path.exists():
            if self.config.create_if_missing:
                self._create_database()
            else:
                raise FileNotFoundError(f"AiBrarian DB not found: {self.db_path}")

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # Try loading sqlite-vec
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            self._conn.load_extension(sqlite_vec.loadable_path())
            self._conn.enable_load_extension(False)

            # Create embedding virtual table
            self._conn.execute(
                EMBEDDING_TABLE_SQL.format(dim=self.config.embedding_dim)
            )
            self._conn.commit()
            self._vec_loaded = True
            self._fallback_vec = None
        except Exception as e:
            from luna.diagnostics.maturity import compiled_debug
            compiled_debug(logger, "sqlite-vec not available for %s: %s", self.config.key, e)
            # Initialize pure-Python fallback for vector search
            try:
                from luna.substrate.vec_fallback import AiBrarianFallbackVec
                self._fallback_vec = AiBrarianFallbackVec(self._conn, self.config.embedding_dim)
                logger.info("Using pure-Python vector fallback for %s", self.config.key)
            except Exception as fb_err:
                logger.debug("Fallback vec init failed for %s: %s", self.config.key, fb_err)
                self._fallback_vec = None

        # Auto-migrate: apply cartridge schema if not present
        try:
            self._conn.execute("SELECT 1 FROM cartridge_meta LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.executescript(CARTRIDGE_SCHEMA)
            self._conn.commit()
            logger.info("Applied cartridge schema to %s", self.config.key)

        # Auto-migrate: apply forge_sync schema if not present
        try:
            self._conn.execute("SELECT 1 FROM forge_sync LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.executescript(FORGE_SYNC_SCHEMA)
            self._conn.commit()
            logger.info("Applied forge_sync schema to %s", self.config.key)

        # Auto-migrate: apply nexus_refs schema if not present (Move 3)
        try:
            self._conn.execute("SELECT 1 FROM nexus_refs LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.executescript(NEXUS_REFS_SCHEMA)
            self._conn.commit()
            logger.info("Applied nexus_refs schema to %s", self.config.key)

        # Auto-migrate: apply extractions_fts if the extractions table exists
        # but the FTS table does not. Older collection DBs predate this index;
        # without it, every extraction-search query in retrieval.py logs a
        # `no such table: extractions_fts` warning and returns nothing.
        try:
            self._conn.execute("SELECT 1 FROM extractions LIMIT 1")
            has_extractions = True
        except sqlite3.OperationalError:
            has_extractions = False
        if has_extractions:
            try:
                self._conn.execute("SELECT 1 FROM extractions_fts LIMIT 1")
            except sqlite3.OperationalError:
                self._conn.executescript(EXTRACTIONS_FTS_MIGRATION)
                # Backfill: triggers only catch future inserts, so seed the
                # FTS index from existing rows.
                self._conn.execute(
                    "INSERT INTO extractions_fts(rowid, content) "
                    "SELECT rowid, content FROM extractions"
                )
                self._conn.commit()
                logger.info("Applied extractions_fts migration to %s", self.config.key)

    def _create_database(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA busy_timeout=15000")
        conn.executescript(STANDARD_SCHEMA)
        if self.config.schema_type == "investigation":
            conn.executescript(INVESTIGATION_SCHEMA)
        conn.executescript(CARTRIDGE_SCHEMA)
        conn.executescript(FORGE_SYNC_SCHEMA)
        conn.executescript(NEXUS_REFS_SCHEMA)
        conn.close()
        logger.info("Created AiBrarian database: %s", self.db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AiBrarian collection not connected")
        return self._conn

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Embedding Generator (thin wrapper — reuses existing patterns)
# ---------------------------------------------------------------------------


class _EmbeddingGenerator:
    """Minimal embedding generator for document chunks."""

    def __init__(self, model: str = "local-minilm", dim: int = 384):
        self.model = model
        self.dim = dim
        self._model_instance = None

    def _load_model(self):
        if self._model_instance is not None:
            return
        if self.model == "local-minilm":
            try:
                from sentence_transformers import SentenceTransformer

                self._model_instance = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                from luna.diagnostics.maturity import compiled_debug
                compiled_debug(logger, "sentence-transformers not installed; embeddings disabled")
                self._model_instance = None
        else:
            logger.warning("Unsupported embedding model: %s", self.model)

    async def generate(self, text: str) -> Optional[list[float]]:
        self._load_model()
        if self._model_instance is None:
            return None
        vec = self._model_instance.encode(text).tolist()
        return vec

    async def generate_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        self._load_model()
        if self._model_instance is None:
            return [None] * len(texts)
        vecs = self._model_instance.encode(texts)
        return [v.tolist() for v in vecs]


# ---------------------------------------------------------------------------
# AiBrarian Engine
# ---------------------------------------------------------------------------


class AiBrarianEngine:
    """
    Universal document database interface.

    SUBSTRATE component — sits alongside MemoryMatrix.
    All surfaces (MCP, Guardian, UI, Sheets, Eclissi) call these methods.
    """

    def __init__(
        self,
        registry_path: str | Path,
        project_root: str | Path | None = None,
    ):
        self.registry = AiBrarianRegistry(registry_path)
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.connections: dict[str, AiBrarianConnection] = {}
        self._generators: dict[str, _EmbeddingGenerator] = {}
        self._lock_in_engine = None  # CollectionLockInEngine — set externally
        self._nexus_registry = None  # NexusRegistry — set externally

        # ForgeCompiler — multi-pass document comprehension
        self._forge_compiler = None
        try:
            from luna.substrate.forge_compiler import ForgeCompiler
            self._forge_compiler = ForgeCompiler()
            if self._forge_compiler.is_available:
                logger.info("[NEXUS] ForgeCompiler available — multi-pass ingestion enabled")
            else:
                logger.info("[NEXUS] ForgeCompiler unavailable — using single-pass extraction")
        except ImportError:
            logger.debug("[NEXUS] ForgeCompiler not available")

    # =====================================================================
    # Lifecycle
    # =====================================================================

    async def initialize(self) -> None:
        """Load registry and connect to all enabled collections."""
        self.registry.load()
        for config in self.registry.list_enabled():
            db_path = self.registry.resolve_db_path(config, self.project_root)
            try:
                conn = AiBrarianConnection(config, db_path)
                await conn.connect()
                self.connections[config.key] = conn
                logger.info("AiBrarian connected: %s (%s)", config.key, config.name)
            except Exception as e:
                logger.warning("Failed to connect AiBrarian collection %s: %s", config.key, e)

        # Discover plugin collections from collections/ directory
        await self._discover_plugin_collections()

    async def _discover_plugin_collections(self) -> None:
        """Scan collections/ directory for plugin collection manifests."""
        collections_dir = self.project_root / "collections"
        if not collections_dir.exists():
            return

        for entry in sorted(collections_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue

            manifest_path = entry / "manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                with open(manifest_path) as f:
                    manifest = yaml.safe_load(f) or {}

                coll_cfg = manifest.get("collection", {})
                key = coll_cfg.get("key", entry.name)

                # Registry-defined collections take precedence
                if key in self.connections:
                    logger.debug("[NEXUS] Plugin '%s' overridden by registry", key)
                    continue

                # Resolve db_path relative to plugin directory
                db_file = coll_cfg.get("db_file", f"{key}.db")
                db_path = entry / db_file

                if not db_path.exists():
                    logger.warning("[NEXUS] Plugin '%s': db not found at %s", key, db_path)
                    continue

                # Build AiBrarianConfig from manifest
                config = AiBrarianConfig(
                    key=key,
                    name=manifest.get("name", key),
                    description=manifest.get("description", ""),
                    db_path=str(db_path),
                    enabled=True,
                    read_only=coll_cfg.get("read_only", False),
                    create_if_missing=False,
                    schema_type=coll_cfg.get("schema_type", "standard"),
                    chunk_size=coll_cfg.get("chunk_size", self.registry.defaults.get("chunk_size", 500)),
                    chunk_overlap=coll_cfg.get("chunk_overlap", self.registry.defaults.get("chunk_overlap", 50)),
                    embedding_model=coll_cfg.get("embedding_model", self.registry.defaults.get("embedding_model", "local-minilm")),
                    embedding_dim=coll_cfg.get("embedding_dim", self.registry.defaults.get("embedding_dim", 384)),
                    tags=coll_cfg.get("tags", []),
                    metadata={"plugin": True, "plugin_dir": str(entry)},
                    ingestion_pattern=coll_cfg.get("ingestion_pattern", "utilitarian"),
                    reflection_mode=coll_cfg.get("reflection_mode", ""),
                    grounding_priority=coll_cfg.get("grounding_priority",
                                                     self.registry.defaults.get("grounding_priority", "supplemental")),
                )

                conn = AiBrarianConnection(config, db_path)
                await conn.connect()
                self.connections[key] = conn
                # Also register in the registry for list_enabled() consistency
                self.registry.collections[key] = config
                logger.info("[NEXUS] Plugin collection loaded: %s (%s)", key, db_path)
            except Exception as e:
                logger.warning("[NEXUS] Failed to load plugin %s: %s", entry.name, e)

    async def reload_registry(self) -> None:
        """Hot-reload registry — connect new, disconnect removed."""
        self.registry.load()
        enabled_keys = {c.key for c in self.registry.list_enabled()}

        # Disconnect removed
        for key in list(self.connections):
            if key not in enabled_keys:
                await self.connections[key].close()
                del self.connections[key]
                logger.info("Disconnected AiBrarian collection: %s", key)

        # Connect new
        for config in self.registry.list_enabled():
            if config.key not in self.connections:
                db_path = self.registry.resolve_db_path(config, self.project_root)
                try:
                    conn = AiBrarianConnection(config, db_path)
                    await conn.connect()
                    self.connections[config.key] = conn
                    logger.info("Connected AiBrarian collection: %s", config.key)
                except Exception as e:
                    logger.warning("Failed to connect AiBrarian collection %s: %s", config.key, e)

    async def shutdown(self) -> None:
        for conn in self.connections.values():
            await conn.close()
        self.connections.clear()

    def set_lock_in_engine(self, engine) -> None:
        """Attach a CollectionLockInEngine for access tracking."""
        self._lock_in_engine = engine

    def set_nexus_registry(self, registry) -> None:
        """Attach a NexusRegistry for unified collection metadata + admission tracking."""
        self._nexus_registry = registry

    async def _bump_collection_access(self, collection_key: str) -> None:
        """Increment access count in collection lock-in table and nexus_registry."""
        if self._lock_in_engine is not None:
            try:
                pattern = "utilitarian"
                if collection_key in self.collections:
                    pattern = self.collections[collection_key].ingestion_pattern
                await self._lock_in_engine.bump_access(collection_key, pattern=pattern)
            except Exception as e:
                logger.debug("Lock-in bump failed for %s: %s", collection_key, e)

        if self._nexus_registry is not None:
            try:
                await self._nexus_registry.bump_access(collection_key)
            except Exception as e:
                logger.debug("NexusRegistry bump failed for %s: %s", collection_key, e)

    async def get_entity_overlap(
        self, collection_key: str, matrix_entities: list[str]
    ) -> int:
        """
        Count entities in this collection that also appear in Memory Matrix.

        Args:
            collection_key: Which collection to check
            matrix_entities: List of entity names from Memory Matrix

        Returns:
            Number of overlapping entities
        """
        if collection_key not in self.connections:
            return 0

        conn = self.connections[collection_key]
        if not matrix_entities:
            return 0

        # Search for each entity in the collection's extractions
        overlap = 0
        for entity in matrix_entities:
            try:
                row = conn.conn.execute(
                    """SELECT COUNT(*) FROM extractions
                       WHERE entity_text LIKE ? LIMIT 1""",
                    (f"%{entity}%",),
                ).fetchone()
                if row and row[0] > 0:
                    overlap += 1
            except Exception:
                pass

        return overlap

    # =====================================================================
    # List / Stats
    # =====================================================================

    def list_collections(self) -> list[dict]:
        result = []
        for config in self.registry.collections.values():
            db_path = self.registry.resolve_db_path(config, self.project_root)
            result.append(
                {
                    "key": config.key,
                    "name": config.name,
                    "description": config.description,
                    "enabled": config.enabled,
                    "connected": config.key in self.connections,
                    "read_only": config.read_only,
                    "db_exists": db_path.exists(),
                    "schema_type": config.schema_type,
                    "tags": config.tags,
                    "project_key": config.project_key,
                    "ingestion_pattern": config.ingestion_pattern,
                }
            )
        return result

    async def stats(self, collection: str) -> dict:
        conn = self._get_conn(collection)
        docs = conn.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = conn.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        words = conn.conn.execute(
            "SELECT COALESCE(SUM(word_count), 0) FROM documents"
        ).fetchone()[0]
        extractions = conn.conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0]
        return {
            "collection": collection,
            "name": conn.config.name,
            "documents": docs,
            "chunks": chunks,
            "total_words": words,
            "extractions": extractions,
        }

    # =====================================================================
    # Search
    # =====================================================================

    async def search(
        self,
        collection: str,
        query: str,
        search_type: str = "hybrid",
        limit: int = 20,
    ) -> list[dict]:
        """
        Search a collection.

        Args:
            collection: Registry key (e.g., "dataroom").
            query: Search query.
            search_type: "keyword", "semantic", or "hybrid" (RRF).
            limit: Max results.
        """
        conn = self._get_conn(collection)

        # Phase 1: Search understanding layer (extractions — claims, summaries)
        extraction_results = self._extraction_search(conn, query, limit=limit // 2)
        chunk_limit = limit - len(extraction_results)

        # Phase 2: Search evidence layer (chunks — raw text)
        if search_type == "keyword":
            chunk_results = self._fts_search(conn, query, chunk_limit)
        elif search_type == "semantic":
            chunk_results = await self._vec_search(conn, query, chunk_limit)
        else:
            kw = self._fts_search(conn, query, chunk_limit)
            sem = await self._vec_search(conn, query, chunk_limit)
            # Filter dead semantics (score ≤ 0.01 = noise from bad embeddings)
            sem_alive = [r for r in sem if r.get("score", 0) > 0.01]
            if not sem_alive:
                chunk_results = kw  # graceful fallback to keyword-only
            else:
                chunk_results = self._rrf_fuse(kw, sem_alive, chunk_limit)

        # Combine: understanding first, evidence second
        results = extraction_results + chunk_results

        # Lock-in hook: bump access on every search
        await self._bump_collection_access(collection)

        return results

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Sanitize query for FTS5: strip punctuation, remove stop words, join with OR."""
        import re
        # Strip everything that isn't a word char or whitespace. FTS5 treats
        # `-` as NOT and `:` as a column qualifier, so a query like
        # `retrieval-augmented generation` parses as `retrieval NOT augmented`
        # and raises `no such column: augmented`. Replacing all non-word
        # punctuation with spaces avoids the entire FTS5-operator class in
        # one pass — hyphenated terms split into separate OR-joinable tokens.
        sanitized = re.sub(r'[^\w\s]', ' ', query)
        # Collapse whitespace
        sanitized = re.sub(r'\s+', ' ', sanitized).strip()
        if not sanitized:
            return query
        # Strip stop words — conversational filler that kills FTS5 implicit-AND matching
        _STOP = {
            'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'do', 'does', 'did', 'have', 'has', 'had', 'having',
            'i', 'me', 'my', 'you', 'your', 'we', 'our', 'they', 'them', 'their',
            'he', 'she', 'it', 'its', 'his', 'her',
            'what', 'which', 'who', 'whom', 'that', 'this', 'these', 'those',
            'am', 'will', 'would', 'shall', 'should', 'can', 'could', 'may', 'might',
            'to', 'of', 'in', 'for', 'on', 'at', 'by', 'with', 'from', 'about',
            'into', 'through', 'during', 'before', 'after', 'above', 'below',
            'and', 'but', 'or', 'nor', 'not', 'so', 'if', 'then',
            'tell', 'know', 'think', 'like', 'just', 'also', 'very', 'really',
            'how', 'when', 'where', 'why', 'there', 'here', 'some', 'any', 'all',
            'more', 'most', 'other', 'than', 'too', 'only', 'each', 'every',
        }
        tokens = [t for t in sanitized.lower().split() if t not in _STOP and len(t) > 1]
        if not tokens:
            # All tokens were stop words — fall back to original minus punctuation
            tokens = sanitized.split()
        # ei↔ie typo expansion — mirrors memory.py:fts5_search so both FTS5
        # paths get the same tolerance (weiner↔wiener, recieve↔receive, etc.)
        expanded: list[str] = []
        seen_t: set[str] = set()
        for t in tokens:
            if t not in seen_t:
                seen_t.add(t)
                expanded.append(t)
            for v in _ei_ie_variants(t):
                if v not in seen_t:
                    seen_t.add(v)
                    expanded.append(v)
        # Join with OR so FTS5 matches any keyword, not all of them
        result = ' OR '.join(expanded)
        logger.info("[FTS5] Input: '%s' → Output: '%s'", query[:60], result[:80])
        return result

    def _extraction_search(
        self, conn: AiBrarianConnection, query: str, limit: int
    ) -> list[dict]:
        """Search the extractions FTS index for comprehension artifacts."""
        try:
            fts_query = self._sanitize_fts_query(query)
            cursor = conn.conn.execute(
                """
                SELECT
                    e.id, e.doc_id, e.node_type, e.content,
                    e.confidence, d.title, d.filename, rank
                FROM extractions_fts
                JOIN extractions e ON extractions_fts.rowid = e.rowid
                JOIN documents d ON e.doc_id = d.id
                WHERE extractions_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            )
            return [
                {
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "filename": row["filename"],
                    "category": row["node_type"],
                    "snippet": row["content"],
                    "score": abs(row["rank"]),
                    "search_type": f"extraction:{row['node_type'].lower()}",
                    "confidence": row["confidence"],
                    "extraction_id": row["id"],
                }
                for row in cursor.fetchall()
            ]
        except sqlite3.OperationalError as e:
            logger.warning("Extraction search failed for '%s': %s", query, e)
            return []

    def _fts_search(
        self, conn: AiBrarianConnection, query: str, limit: int
    ) -> list[dict]:
        try:
            fts_query = self._sanitize_fts_query(query)
            cursor = conn.conn.execute(
                """
                SELECT
                    d.id, d.title, d.filename, d.category,
                    c.chunk_text,
                    snippet(chunks_fts, 0, '>>>', '<<<', '...', 32) AS match_highlight,
                    bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                JOIN documents d ON c.doc_id = d.id
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (fts_query, limit),
            )
            return [
                {
                    "doc_id": row["id"],
                    "title": row["title"],
                    "filename": row["filename"],
                    "category": row["category"],
                    "snippet": row["chunk_text"],
                    "score": -row["score"],
                    "search_type": "keyword",
                }
                for row in cursor.fetchall()
            ]
        except sqlite3.OperationalError as e:
            logger.warning("FTS search failed for '%s': %s", query, e)
            return []

    async def _vec_search(
        self, conn: AiBrarianConnection, query: str, limit: int
    ) -> list[dict]:
        if not conn._vec_loaded and not getattr(conn, '_fallback_vec', None):
            return []

        gen = self._get_generator(conn.config)
        query_vec = await gen.generate(query)
        if query_vec is None:
            return []

        blob = _vector_to_blob(query_vec)

        # Use fallback if sqlite-vec extension isn't loaded
        fallback = getattr(conn, '_fallback_vec', None)
        if not conn._vec_loaded and fallback:
            try:
                fb_results = fallback.search(query_vec, limit)
                results = []
                seen_docs: set[str] = set()
                for chunk_id, distance in fb_results:
                    if ":" in chunk_id:
                        # Synthetic id (:chunk:N or :section:N) — both live
                        # in the chunks table; bare doc_id UUIDs have no `:`.
                        doc_row = conn.conn.execute(
                            "SELECT d.id, d.title, d.filename, d.category, c.chunk_text "
                            "FROM chunks c JOIN documents d ON c.doc_id = d.id WHERE c.id = ?",
                            (chunk_id,),
                        ).fetchone()
                    else:
                        doc_row = conn.conn.execute(
                            "SELECT d.id, d.title, d.filename, d.category, "
                            "substr(d.full_text, 1, 200) AS chunk_text FROM documents d WHERE d.id = ?",
                            (chunk_id,),
                        ).fetchone()
                    if doc_row and doc_row["id"] not in seen_docs:
                        seen_docs.add(doc_row["id"])
                        results.append({
                            "doc_id": doc_row["id"],
                            "title": doc_row["title"],
                            "filename": doc_row["filename"],
                            "category": doc_row["category"],
                            "snippet": doc_row["chunk_text"],
                            "score": max(0.0, 1.0 - distance),
                            "search_type": "semantic",
                        })
                return results
            except Exception as e:
                logger.warning("Fallback vector search failed: %s", e)
                return []

        try:
            cursor = conn.conn.execute(
                """
                SELECT chunk_id, distance
                FROM chunk_embeddings
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (blob, limit),
            )
            results = []
            seen_docs: set[str] = set()
            for row in cursor.fetchall():
                chunk_id = row[0]
                distance = row[1]

                if ":" in chunk_id:
                    # Synthetic id (:chunk:N or :section:N) — both live in
                    # the chunks table; bare doc_id UUIDs have no `:`.
                    doc_row = conn.conn.execute(
                        """
                        SELECT d.id, d.title, d.filename, d.category, c.chunk_text
                        FROM chunks c
                        JOIN documents d ON c.doc_id = d.id
                        WHERE c.id = ?
                        """,
                        (chunk_id,),
                    ).fetchone()
                else:
                    # Doc-level average embedding — chunk_id IS the doc id
                    doc_row = conn.conn.execute(
                        """
                        SELECT d.id, d.title, d.filename, d.category,
                               substr(d.full_text, 1, 200) AS chunk_text
                        FROM documents d
                        WHERE d.id = ?
                        """,
                        (chunk_id,),
                    ).fetchone()

                if doc_row and doc_row["id"] not in seen_docs:
                    seen_docs.add(doc_row["id"])
                    results.append(
                        {
                            "doc_id": doc_row["id"],
                            "title": doc_row["title"],
                            "filename": doc_row["filename"],
                            "category": doc_row["category"],
                            "snippet": doc_row["chunk_text"],
                            "score": max(0.0, 1.0 - distance),
                            "search_type": "semantic",
                        }
                    )
            return results
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            return []

    @staticmethod
    def _rrf_fuse(
        kw_results: list[dict],
        sem_results: list[dict],
        limit: int,
        k: int = 60,
    ) -> list[dict]:
        scores: dict[str, dict] = {}
        for rank, r in enumerate(kw_results):
            key = r["doc_id"]
            scores[key] = {"data": r, "score": 1 / (k + rank + 1)}
        for rank, r in enumerate(sem_results):
            key = r["doc_id"]
            if key not in scores:
                scores[key] = {"data": r, "score": 0}
            scores[key]["score"] += 1 / (k + rank + 1)

        sorted_results = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        return [
            {**item["data"], "score": item["score"], "search_type": "hybrid"}
            for item in sorted_results[:limit]
        ]

    # =====================================================================
    # Similarity
    # =====================================================================

    async def similar(
        self, collection: str, doc_id: str, limit: int = 10
    ) -> list[dict]:
        """Find documents similar to a given document."""
        conn = self._get_conn(collection)
        if not conn._vec_loaded:
            return []

        # Get average embedding for the document
        row = conn.conn.execute(
            "SELECT embedding FROM chunk_embeddings WHERE chunk_id = ?",
            (doc_id,),
        ).fetchone()
        if not row:
            chunk_rows = conn.conn.execute(
                """
                SELECT ce.embedding
                FROM chunk_embeddings ce
                JOIN chunks c ON ce.chunk_id = c.id
                WHERE c.doc_id = ?
                """,
                (doc_id,),
            ).fetchall()
            if not chunk_rows:
                return []
            import numpy as np

            vecs = [_blob_to_vector(r[0]) for r in chunk_rows]
            avg_vec = np.mean(vecs, axis=0).tolist()
        else:
            avg_vec = _blob_to_vector(row[0])

        blob = _vector_to_blob(avg_vec)
        cursor = conn.conn.execute(
            """
            SELECT chunk_id, distance
            FROM chunk_embeddings
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
            """,
            (blob, limit * 3),
        )

        seen_docs: set[str] = set()
        results = []
        for r in cursor.fetchall():
            chunk_id = r[0]
            distance = r[1]
            doc_row = conn.conn.execute(
                """
                SELECT d.id, d.title, d.filename, d.category
                FROM chunks c JOIN documents d ON c.doc_id = d.id
                WHERE c.id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if doc_row and doc_row["id"] != doc_id and doc_row["id"] not in seen_docs:
                seen_docs.add(doc_row["id"])
                results.append(
                    {
                        "doc_id": doc_row["id"],
                        "title": doc_row["title"],
                        "filename": doc_row["filename"],
                        "category": doc_row["category"],
                        "similarity": 1.0 - distance,
                    }
                )
                if len(results) >= limit:
                    break
        return results

    # =====================================================================
    # Co-occurrence
    # =====================================================================

    async def co_occurrence(
        self, collection: str, terms: list[str], limit: int = 20
    ) -> list[dict]:
        """Find documents containing ALL specified terms."""
        conn = self._get_conn(collection)
        fts_query = " AND ".join(f'"{t}"' for t in terms)
        try:
            cursor = conn.conn.execute(
                """
                SELECT DISTINCT d.id, d.title, d.filename, d.category, d.word_count
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                JOIN documents d ON c.doc_id = d.id
                WHERE chunks_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError as e:
            logger.warning("Co-occurrence search failed: %s", e)
            return []

    # =====================================================================
    # Forge Sync — file change tracking for watch/compile mode
    # =====================================================================

    _LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50 MB

    async def _check_sync_status(
        self, collection: str, file_path: Path
    ) -> str:
        """Returns: 'unchanged', 'modified', 'new', or 'deleted'."""
        conn = self._get_conn(collection)
        row = conn.conn.execute(
            "SELECT file_hash, last_modified FROM forge_sync WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()

        if not row:
            return "new"

        if not file_path.exists():
            return "deleted"

        # For large files, compare mtime only to avoid memory issues
        if file_path.stat().st_size > self._LARGE_FILE_THRESHOLD:
            if file_path.stat().st_mtime != row["last_modified"]:
                return "modified"
            return "unchanged"

        import hashlib
        current_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if current_hash != row["file_hash"]:
            return "modified"

        return "unchanged"

    async def _update_sync(
        self, collection: str, file_path: Path, doc_id: str
    ) -> None:
        """Record a successful ingest in forge_sync."""
        import hashlib
        from datetime import datetime

        file_path = Path(file_path)
        stat = file_path.stat()

        if stat.st_size > self._LARGE_FILE_THRESHOLD:
            file_hash = f"mtime:{stat.st_mtime}"
        else:
            file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()

        conn = self._get_conn(collection)
        conn.conn.execute(
            """INSERT OR REPLACE INTO forge_sync
               (file_path, file_hash, file_size, last_modified, last_synced, doc_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'synced')""",
            (str(file_path), file_hash, stat.st_size,
             stat.st_mtime, datetime.now().isoformat(), doc_id),
        )
        conn.conn.commit()

    async def _delete_document(self, collection: str, doc_id: str) -> None:
        """Delete a document and all its chunks/extractions/entities."""
        conn = self._get_conn(collection)
        conn.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.conn.execute("DELETE FROM extractions WHERE doc_id = ?", (doc_id,))
        try:
            conn.conn.execute("DELETE FROM entities WHERE doc_id = ?", (doc_id,))
        except sqlite3.OperationalError:
            pass  # entities table may not exist
        conn.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.conn.commit()

    # =====================================================================
    # Ingestion
    # =====================================================================

    async def ingest(
        self,
        collection: str,
        file_path: Path,
        metadata: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Ingest a document: read -> chunk -> embed -> extract.

        Returns the doc_id on success, None if skipped.
        """
        conn = self._get_conn(collection)
        if conn.config.read_only:
            raise PermissionError(f"Collection '{collection}' is read-only")

        metadata = metadata or {}
        file_path = Path(file_path)

        # 1. Read
        content = read_file_content(file_path)
        if not content or len(content.strip()) < 50:
            logger.info("Skipping %s (too short or unreadable)", file_path.name)
            return None

        # 2. Document record
        doc_id = str(uuid.uuid4())
        word_count = len(content.split())
        conn.conn.execute(
            """
            INSERT OR REPLACE INTO documents
                (id, filename, title, full_text, word_count, source_path,
                 doc_type, category, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                doc_id,
                file_path.name,
                metadata.get("title", file_path.stem),
                content,
                word_count,
                str(file_path),
                file_path.suffix.lower().lstrip("."),
                metadata.get("category"),
                json.dumps(metadata),
            ),
        )

        # 3. Delete old chunks + extractions (latest-wins)
        conn.conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        conn.conn.execute("DELETE FROM extractions WHERE doc_id = ?", (doc_id,))

        # 3.5 Detect document structure
        structure = detect_document_structure(content)
        if structure:
            logger.info(
                "Detected %d structural markers in %s",
                len(structure), file_path.name,
            )

        # 4. Chunk
        chunks = chunk_document(
            content,
            chunk_size=conn.config.chunk_size,
            overlap=conn.config.chunk_overlap,
            source_id=doc_id,
            structure=structure,
        )

        for chunk in chunks:
            chunk_id = f"{doc_id}:chunk:{chunk.index}"
            conn.conn.execute(
                """
                INSERT INTO chunks
                    (id, doc_id, chunk_index, chunk_text, word_count,
                     start_char, end_char, section_label, section_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    doc_id,
                    chunk.index,
                    chunk.text,
                    chunk.word_count,
                    chunk.start_char,
                    chunk.end_char,
                    chunk.section_label or None,
                    chunk.section_level or None,
                ),
            )

        # 5. Embed chunks
        await self._embed_chunks(conn, doc_id, chunks)

        conn.conn.commit()

        # 6. Run extraction if enabled for this collection
        use_compiler = (
            conn.config.extract_on_ingest
            and conn.config.multi_pass_ingest
            and self._forge_compiler is not None
            and self._forge_compiler.is_available
        )

        if use_compiler:
            # Multi-pass comprehension via ForgeCompiler
            compilation = await self._forge_compiler.compile(
                text=content,
                filename=file_path.name,
            )
            # Write extractions
            for idx, ex in enumerate(compilation.extractions):
                ext_id = f"{doc_id}:ext:{ex['node_type'].lower()}:{idx}"
                conn.conn.execute(
                    "INSERT OR REPLACE INTO extractions "
                    "(id, doc_id, chunk_index, node_type, content, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ext_id, doc_id, 0, ex["node_type"], ex["content"], ex["confidence"]),
                )
            # Write entities from entity pass
            for idx, ent in enumerate(compilation.entity_rows):
                ent_id = f"{doc_id}:ent:compiler:{idx}"
                try:
                    conn.conn.execute(
                        "INSERT OR REPLACE INTO entities "
                        "(id, doc_id, entity_type, entity_value, confidence) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (ent_id, doc_id, ent["entity_type"], ent["entity_value"], ent["confidence"]),
                    )
                except Exception:
                    pass  # entities table may not exist
            conn.conn.commit()
            logger.info(
                "[INGEST] ForgeCompiler: %d extractions, %d entities from %d passes for %s",
                len(compilation.extractions), len(compilation.entity_rows),
                compilation.total_haiku_calls, file_path.name,
            )
        elif conn.config.extract_on_ingest:
            # Fallback: single-pass extraction
            await self.extract(collection, doc_id)

            # Generate TABLE_OF_CONTENTS from detected structure
            if structure:
                toc_lines = []
                for marker in structure:
                    indent = "  " * (marker.level - 1)
                    page = f" (p. {marker.page_hint})" if marker.page_hint else ""
                    toc_lines.append(f"{indent}{marker.heading}{page}")

                toc_content = "Document Structure:\n" + "\n".join(toc_lines)
                toc_id = f"{doc_id}:ext:toc:0"
                conn.conn.execute(
                    "INSERT OR REPLACE INTO extractions "
                    "(id, doc_id, chunk_index, node_type, content, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (toc_id, doc_id, 0, "TABLE_OF_CONTENTS", toc_content, 1.0),
                )
                conn.conn.commit()

        logger.info(
            "Ingested %s → %s (%d words, %d chunks)",
            file_path.name,
            doc_id,
            word_count,
            len(chunks),
        )
        return doc_id

    # Directories to exclude from directory ingestion / watch
    _EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".env", ".tox"}

    async def ingest_directory(
        self,
        collection: str,
        directory: Path,
        recursive: bool = True,
        extensions: Optional[set[str]] = None,
        sync_aware: bool = True,
    ) -> list[str]:
        """Ingest all supported files from a directory.

        When *sync_aware* is True (default), files that haven't changed since
        the last ingest are skipped.  Modified files are re-ingested (old doc
        deleted first).
        """
        extensions = extensions or {".md", ".txt", ".csv", ".pdf", ".docx", ".json", ".yaml", ".yml"}
        directory = Path(directory)
        doc_ids: list[str] = []
        skipped = 0

        pattern = "**/*" if recursive else "*"
        for file_path in sorted(directory.glob(pattern)):
            # Skip excluded directories
            if any(part in self._EXCLUDED_DIRS or part.startswith(".") for part in file_path.parts):
                continue

            if not file_path.is_file() or file_path.suffix.lower() not in extensions:
                continue

            # Sync-aware check
            if sync_aware:
                status = await self._check_sync_status(collection, file_path)
                if status == "unchanged":
                    skipped += 1
                    continue
                if status == "modified":
                    # Delete old doc before re-ingesting
                    conn = self._get_conn(collection)
                    row = conn.conn.execute(
                        "SELECT doc_id FROM forge_sync WHERE file_path = ?",
                        (str(file_path),),
                    ).fetchone()
                    if row and row["doc_id"]:
                        await self._delete_document(collection, row["doc_id"])

            category = file_path.parent.name if file_path.parent != directory else ""
            doc_id = await self.ingest(
                collection,
                file_path,
                metadata={"title": file_path.stem, "category": category},
            )
            if doc_id:
                await self._update_sync(collection, file_path, doc_id)
                doc_ids.append(doc_id)

        logger.info(
            "Ingested %d files from %s into '%s' (skipped %d unchanged)",
            len(doc_ids), directory, collection, skipped,
        )
        return doc_ids

    # =====================================================================
    # Document Retrieval
    # =====================================================================

    async def get_document(self, collection: str, doc_id: str) -> Optional[dict]:
        """Return full document record by ID."""
        conn = self._get_conn(collection)
        row = conn.conn.execute(
            """SELECT id, filename, title, full_text, word_count,
                      source_path, doc_type, category, status, metadata,
                      created_at, updated_at
               FROM documents WHERE id = ?""",
            (doc_id,),
        ).fetchone()

        if row:
            # Lock-in hook: bump access on document open
            await self._bump_collection_access(collection)

        return dict(row) if row else None

    async def list_documents(
        self, collection: str, skip: int = 0, limit: int = 50
    ) -> dict:
        """Paginated document listing (without full_text)."""
        conn = self._get_conn(collection)
        total = conn.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        rows = conn.conn.execute(
            """SELECT id, filename, title, word_count, category, doc_type,
                      status, created_at
               FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, skip),
        ).fetchall()
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "documents": [dict(r) for r in rows],
        }

    # =====================================================================
    # Count & Term Stats
    # =====================================================================

    async def count(
        self, collection: str, query: str, search_type: str = "keyword"
    ) -> int:
        """Fast count of matching documents without returning results."""
        conn = self._get_conn(collection)
        if search_type == "keyword":
            try:
                row = conn.conn.execute(
                    """SELECT COUNT(DISTINCT d.id)
                       FROM chunks_fts
                       JOIN chunks c ON chunks_fts.rowid = c.rowid
                       JOIN documents d ON c.doc_id = d.id
                       WHERE chunks_fts MATCH ?""",
                    (query,),
                ).fetchone()
                return row[0] if row else 0
            except sqlite3.OperationalError:
                return 0
        else:
            results = await self.search(collection, query, search_type, 1000)
            return len(results)

    async def term_stats(self, collection: str, terms: list[str]) -> dict[str, int]:
        """Document counts for multiple terms."""
        conn = self._get_conn(collection)
        counts: dict[str, int] = {}
        for term in terms:
            try:
                row = conn.conn.execute(
                    """SELECT COUNT(DISTINCT d.id)
                       FROM chunks_fts
                       JOIN chunks c ON chunks_fts.rowid = c.rowid
                       JOIN documents d ON c.doc_id = d.id
                       WHERE chunks_fts MATCH ?""",
                    (f'"{term}"',),
                ).fetchone()
                counts[term] = row[0] if row else 0
            except sqlite3.OperationalError:
                counts[term] = 0
        return counts

    # =====================================================================
    # Entity Extraction
    # =====================================================================

    async def top_entities(
        self, collection: str, limit: int = 50, sample_size: int = 100
    ) -> dict:
        """Extract and rank entities across a sample of documents."""
        from luna.substrate.aibrarian_extractors import (
            extract_persons, extract_organizations,
        )
        conn = self._get_conn(collection)
        rows = conn.conn.execute(
            """SELECT id, full_text FROM documents
               WHERE full_text IS NOT NULL AND full_text != ''
               ORDER BY RANDOM() LIMIT ?""",
            (sample_size,),
        ).fetchall()

        person_counts: Counter = Counter()
        org_counts: Counter = Counter()

        for _, text in rows:
            if text:
                for p in extract_persons(text):
                    person_counts[p] += 1
                for o in extract_organizations(text):
                    org_counts[o] += 1

        return {
            "persons": [
                {"text": name, "count": c}
                for name, c in person_counts.most_common(limit)
            ],
            "organizations": [
                {"text": name, "count": c}
                for name, c in org_counts.most_common(limit)
            ],
            "total_documents_analyzed": len(rows),
        }

    async def search_entity(
        self, collection: str, name: str, limit: int = 50
    ) -> dict:
        """Find documents mentioning a specific entity."""
        conn = self._get_conn(collection)
        search_term = f"%{name}%"
        rows = conn.conn.execute(
            """SELECT id, title, filename, word_count,
                      substr(full_text, 1, 300) as snippet
               FROM documents WHERE full_text LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (search_term, limit),
        ).fetchall()

        total = conn.conn.execute(
            "SELECT COUNT(*) FROM documents WHERE full_text LIKE ?",
            (search_term,),
        ).fetchone()[0]

        return {
            "entity": name,
            "document_count": total,
            "documents": [dict(r) for r in rows],
        }

    async def document_entities(self, collection: str, doc_id: str) -> dict:
        """Extract entities from a specific document."""
        from luna.substrate.aibrarian_extractors import (
            extract_persons, extract_organizations, extract_dates_simple,
        )
        conn = self._get_conn(collection)
        row = conn.conn.execute(
            "SELECT full_text FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row or not row["full_text"]:
            return {"doc_id": doc_id, "persons": [], "dates": [], "organizations": [], "total": 0}

        text = row["full_text"]
        persons = extract_persons(text)
        orgs = extract_organizations(text)
        dates = extract_dates_simple(text)
        return {
            "doc_id": doc_id,
            "persons": persons,
            "dates": dates,
            "organizations": orgs,
            "total": len(persons) + len(orgs) + len(dates),
        }

    # =====================================================================
    # Timeline
    # =====================================================================

    async def timeline(
        self,
        collection: str,
        query: str,
        limit: int = 100,
        confidence: Optional[str] = None,
    ) -> dict:
        """Extract dates from documents matching a search query."""
        from luna.substrate.aibrarian_extractors import extract_dates_from_text

        conn = self._get_conn(collection)
        try:
            rows = conn.conn.execute(
                """SELECT DISTINCT d.id, d.full_text
                   FROM chunks_fts
                   JOIN chunks c ON chunks_fts.rowid = c.rowid
                   JOIN documents d ON c.doc_id = d.id
                   WHERE chunks_fts MATCH ? LIMIT 200""",
                (query,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        all_events = []
        for doc_id, full_text in rows:
            if full_text:
                all_events.extend(extract_dates_from_text(full_text, doc_id))

        if confidence:
            all_events = [e for e in all_events if e.confidence == confidence.lower()]

        all_events.sort(key=lambda e: e.date)
        all_events = all_events[:limit]

        return {
            "query": query,
            "total_events": len(all_events),
            "events": [
                {"date": e.date, "context": e.context, "doc_id": e.doc_id,
                 "confidence": e.confidence, "date_format": e.date_format}
                for e in all_events
            ],
        }

    async def timeline_range(
        self,
        collection: str,
        start: str,
        end: str,
        limit: int = 100,
        confidence: Optional[str] = None,
    ) -> dict:
        """Get events within a date range across all documents."""
        from luna.substrate.aibrarian_extractors import extract_dates_from_text
        from datetime import datetime as dt

        start_date = dt.strptime(start, "%Y-%m-%d")
        end_date = dt.strptime(end, "%Y-%m-%d")

        conn = self._get_conn(collection)
        rows = conn.conn.execute(
            "SELECT id, full_text FROM documents WHERE full_text IS NOT NULL AND full_text != ''"
        ).fetchall()

        all_events = []
        for doc_id, full_text in rows:
            if full_text:
                for e in extract_dates_from_text(full_text, doc_id):
                    try:
                        event_date = dt.strptime(e.date, "%Y-%m-%d")
                        if start_date <= event_date <= end_date:
                            all_events.append(e)
                    except ValueError:
                        continue

        if confidence:
            all_events = [e for e in all_events if e.confidence == confidence.lower()]

        all_events.sort(key=lambda e: e.date)
        all_events = all_events[:limit]

        return {
            "total_events": len(all_events),
            "events": [
                {"date": e.date, "context": e.context, "doc_id": e.doc_id,
                 "confidence": e.confidence, "date_format": e.date_format}
                for e in all_events
            ],
        }

    async def document_timeline(
        self, collection: str, doc_id: str, confidence: Optional[str] = None
    ) -> dict:
        """Get all dates extracted from a specific document."""
        from luna.substrate.aibrarian_extractors import extract_dates_from_text

        conn = self._get_conn(collection)
        row = conn.conn.execute(
            "SELECT full_text FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row or not row["full_text"]:
            return {"doc_id": doc_id, "total_events": 0, "events": []}

        events = extract_dates_from_text(row["full_text"], doc_id)
        if confidence:
            events = [e for e in events if e.confidence == confidence.lower()]

        return {
            "doc_id": doc_id,
            "total_events": len(events),
            "events": [
                {"date": e.date, "context": e.context, "doc_id": e.doc_id,
                 "confidence": e.confidence, "date_format": e.date_format}
                for e in events
            ],
        }

    # =====================================================================
    # Analytics
    # =====================================================================

    async def word_frequency(
        self, collection: str, query: str, top: int = 50
    ) -> dict:
        """Word frequency analysis on matching documents."""
        from luna.substrate.aibrarian_extractors import tokenize

        conn = self._get_conn(collection)
        try:
            rows = conn.conn.execute(
                """SELECT c.chunk_text
                   FROM chunks_fts
                   JOIN chunks c ON chunks_fts.rowid = c.rowid
                   WHERE chunks_fts MATCH ? LIMIT 500""",
                (query,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        all_words: list[str] = []
        for (text,) in rows:
            if text:
                all_words.extend(tokenize(text))

        word_counts = Counter(all_words)
        total = len(all_words)
        unique = len(word_counts)

        return {
            "query": query,
            "total_words": total,
            "unique_words": unique,
            "frequencies": [
                {"word": w, "count": c, "percentage": round((c / total) * 100, 2) if total else 0}
                for w, c in word_counts.most_common(top)
            ],
        }

    async def ngrams(
        self, collection: str, query: str, n: int = 2, top: int = 30
    ) -> dict:
        """N-gram analysis on matching documents."""
        from luna.substrate.aibrarian_extractors import tokenize, extract_ngrams

        conn = self._get_conn(collection)
        try:
            rows = conn.conn.execute(
                """SELECT c.chunk_text
                   FROM chunks_fts
                   JOIN chunks c ON chunks_fts.rowid = c.rowid
                   WHERE chunks_fts MATCH ? LIMIT 500""",
                (query,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        all_ngrams: list[str] = []
        for (text,) in rows:
            if text:
                words = tokenize(text)
                all_ngrams.extend(extract_ngrams(words, n))

        ngram_counts = Counter(all_ngrams)
        return {
            "query": query,
            "n": n,
            "ngrams": [
                {"phrase": phrase, "count": c}
                for phrase, c in ngram_counts.most_common(top)
            ],
        }

    async def wordcloud(
        self, collection: str, query: str, top: int = 100
    ) -> dict:
        """Wordcloud-formatted word frequency data."""
        from luna.substrate.aibrarian_extractors import tokenize

        conn = self._get_conn(collection)
        try:
            rows = conn.conn.execute(
                """SELECT c.chunk_text
                   FROM chunks_fts
                   JOIN chunks c ON chunks_fts.rowid = c.rowid
                   WHERE chunks_fts MATCH ? LIMIT 500""",
                (query,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        all_words: list[str] = []
        for (text,) in rows:
            if text:
                all_words.extend(tokenize(text))

        word_counts = Counter(all_words)
        return {
            "query": query,
            "words": [
                {"text": w, "value": c}
                for w, c in word_counts.most_common(top)
            ],
        }

    async def compare_terms(
        self,
        collection: str,
        terms: list[str],
        context_window: int = 5,
        top: int = 20,
    ) -> dict:
        """Compare word contexts for multiple terms."""
        from luna.substrate.aibrarian_extractors import get_context_words, tokenize

        results = []
        conn = self._get_conn(collection)

        for term in terms:
            try:
                rows = conn.conn.execute(
                    """SELECT c.chunk_text
                       FROM chunks_fts
                       JOIN chunks c ON chunks_fts.rowid = c.rowid
                       WHERE chunks_fts MATCH ? LIMIT 200""",
                    (f'"{term}"',),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

            doc_count = len(rows)
            all_context: list[str] = []
            for (text,) in rows:
                if text:
                    all_context.extend(get_context_words(text, term, context_window))

            context_counts = Counter(all_context)
            total_context = len(all_context)

            results.append({
                "term": term,
                "doc_count": doc_count,
                "context_words": [
                    {"word": w, "count": c,
                     "percentage": round((c / total_context) * 100, 2) if total_context else 0}
                    for w, c in context_counts.most_common(top)
                ],
            })

        return {"terms": results}

    # =====================================================================
    # Batch Similarity
    # =====================================================================

    async def batch_similarity(
        self, collection: str, doc_ids: list[str]
    ) -> dict:
        """Pairwise similarity scores between multiple documents."""
        conn = self._get_conn(collection)
        if not conn._vec_loaded:
            return {"documents": doc_ids, "pairs": [], "count": 0}

        try:
            import numpy as np
        except ImportError:
            return {"documents": doc_ids, "pairs": [], "count": 0}

        # Get average embeddings per document
        doc_embeddings: dict[str, list[float]] = {}
        for did in doc_ids:
            chunk_rows = conn.conn.execute(
                """SELECT ce.embedding FROM chunk_embeddings ce
                   JOIN chunks c ON ce.chunk_id = c.id
                   WHERE c.doc_id = ?""",
                (did,),
            ).fetchall()
            if chunk_rows:
                vecs = [_blob_to_vector(r[0]) for r in chunk_rows]
                doc_embeddings[did] = np.mean(vecs, axis=0).tolist()
            else:
                # Try doc-level embedding
                row = conn.conn.execute(
                    "SELECT embedding FROM chunk_embeddings WHERE chunk_id = ?",
                    (did,),
                ).fetchone()
                if row:
                    doc_embeddings[did] = _blob_to_vector(row[0])

        pairs = []
        keys = list(doc_embeddings.keys())
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                vec_a = np.array(doc_embeddings[a])
                vec_b = np.array(doc_embeddings[b])
                sim = float(np.dot(vec_a, vec_b) / (np.linalg.norm(vec_a) * np.linalg.norm(vec_b) + 1e-9))
                pairs.append({"doc_a": a, "doc_b": b, "similarity": round(sim, 4)})

        pairs.sort(key=lambda p: p["similarity"], reverse=True)
        return {"documents": doc_ids, "pairs": pairs, "count": len(pairs)}

    # =====================================================================
    # Export
    # =====================================================================

    async def export_search(
        self, collection: str, query: str, limit: int = 1000, fmt: str = "json"
    ) -> dict:
        """Export search results as structured data."""
        results = await self.search(collection, query, "hybrid", limit)
        return {"query": query, "total_results": len(results), "format": fmt, "results": results}

    async def export_document(
        self, collection: str, doc_id: str, fmt: str = "json"
    ) -> Optional[dict]:
        """Export a single document."""
        return await self.get_document(collection, doc_id)

    # =====================================================================
    # Read-Only SQL
    # =====================================================================

    async def execute_sql(
        self, collection: str, query: str, limit: int = 100
    ) -> dict:
        """Execute read-only SQL against a collection's database."""
        from luna.substrate.aibrarian_extractors import validate_readonly_sql
        import time

        error = validate_readonly_sql(query)
        if error:
            raise PermissionError(error)

        conn = self._get_conn(collection)
        if "LIMIT" not in query.upper():
            query = f"{query} LIMIT {limit}"

        start = time.time()
        cursor = conn.conn.execute(query)
        rows = [dict(r) for r in cursor.fetchmany(limit)]
        elapsed = (time.time() - start) * 1000

        return {
            "query": query,
            "rows": rows,
            "row_count": len(rows),
            "execution_time_ms": round(elapsed, 2),
        }

    # =====================================================================
    # Extraction (calls Scribe via message interface)
    # =====================================================================

    async def extract(
        self, collection: str, doc_id: str, force: bool = False
    ) -> list[dict]:
        """
        Run document comprehension extraction via Haiku.
        Reads document chunks, processes in batches, stores
        summaries + claims + entities in collection DB.
        """
        import asyncio

        conn = self._get_conn(collection)
        if conn.config.read_only:
            raise PermissionError(f"Collection '{collection}' is read-only")

        if not force:
            existing = conn.conn.execute(
                "SELECT COUNT(*) FROM extractions WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]
            if existing > 0:
                logger.info(
                    "Doc %s already has %d extractions", doc_id, existing
                )
                return []

        # Delete old extractions + entities
        conn.conn.execute(
            "DELETE FROM extractions WHERE doc_id = ?", (doc_id,)
        )
        conn.conn.execute(
            "DELETE FROM entities WHERE doc_id = ?", (doc_id,)
        )

        # Get chunks for this document
        rows = conn.conn.execute(
            "SELECT chunk_index, chunk_text FROM chunks "
            "WHERE doc_id = ? ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
        if not rows:
            logger.warning("No chunks found for doc %s", doc_id)
            return []

        # Build section label lookup from chunks
        section_rows = conn.conn.execute(
            "SELECT chunk_index, section_label FROM chunks "
            "WHERE doc_id = ? AND section_label IS NOT NULL "
            "ORDER BY chunk_index",
            (doc_id,),
        ).fetchall()
        section_lookup = {r["chunk_index"]: r["section_label"] for r in section_rows}

        # Initialize Anthropic client
        try:
            import anthropic

            client = anthropic.Anthropic()
        except Exception as e:
            logger.error(
                "Document extraction failed — no Anthropic client: %s", e
            )
            return []

        # Map config model names to API model strings
        model_map = {
            "claude-haiku": "claude-haiku-4-5-20251001",
            "claude-sonnet": "claude-sonnet-4-5-20250929",
        }
        api_model = model_map.get(
            conn.config.extraction_model, conn.config.extraction_model
        )

        # Batch chunks (5 per batch ~ 2500 words)
        BATCH_SIZE = 5
        batches: list[tuple[str, list[int]]] = []
        for i in range(0, len(rows), BATCH_SIZE):
            batch_rows = rows[i : i + BATCH_SIZE]
            batch_text = "\n\n".join(r["chunk_text"] for r in batch_rows)
            batch_indices = [r["chunk_index"] for r in batch_rows]
            batches.append((batch_text, batch_indices))

        all_extractions: list[dict] = []

        for batch_idx, (batch_text, chunk_indices) in enumerate(batches):
            # Skip very short batches (title pages, etc.)
            if len(batch_text.strip()) < 100:
                continue

            is_first = batch_idx == 0

            # Determine section context for this batch
            batch_section = ""
            for ci in chunk_indices:
                if ci in section_lookup:
                    batch_section = section_lookup[ci]
                    break
            # If no direct match, find nearest preceding section
            if not batch_section and section_lookup:
                preceding = [
                    (idx, label) for idx, label in section_lookup.items()
                    if idx <= chunk_indices[0]
                ]
                if preceding:
                    batch_section = max(preceding, key=lambda x: x[0])[1]

            section_hint = (
                f"\n\nDOCUMENT LOCATION: This text is from \"{batch_section}\".\n"
                f"Include this section name in your summary.\n"
                if batch_section else ""
            )

            user_msg = (
                (
                    "This is the OPENING section of the document. "
                    "Provide a thorough summary of the document's subject, "
                    "scope, and main argument as best you can determine. "
                    "Also extract claims and entities.\n\n"
                )
                if is_first
                else f"Extract knowledge from this section of the document:{section_hint}\n\n"
            ) + batch_text

            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda msg=user_msg: client.messages.create(
                        model=api_model,
                        max_tokens=16384,
                        temperature=0.2,
                        system=DOCUMENT_EXTRACTION_PROMPT,
                        messages=[{"role": "user", "content": msg}],
                    ),
                )
                response_text = response.content[0].text
            except Exception as e:
                logger.warning(
                    "Extraction batch %d failed: %s", batch_idx, e
                )
                continue

            # Parse JSON response
            try:
                text = response_text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    end_idx = len(lines) - 1
                    for i in range(len(lines) - 1, 0, -1):
                        if lines[i].strip().startswith("```"):
                            end_idx = i
                            break
                    text = "\n".join(lines[1:end_idx])
                json_start = text.find("{")
                json_end = text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    text = text[json_start:json_end]
                text = re.sub(r",\s*([}\]])", r"\1", text)
                data = json.loads(text)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(
                    "Extraction JSON parse failed batch %d: %s", batch_idx, e
                )
                continue

            chunk_ref = chunk_indices[0]  # Reference first chunk in batch

            # Store summary
            ext_metadata = json.dumps({"section": batch_section}) if batch_section else None
            summary = data.get("summary", "").strip()
            if summary:
                ext_id = f"{doc_id}:ext:summary:{batch_idx}"
                node_type = (
                    "DOCUMENT_SUMMARY" if is_first else "SECTION_SUMMARY"
                )
                conn.conn.execute(
                    "INSERT OR REPLACE INTO extractions "
                    "(id, doc_id, chunk_index, node_type, content, confidence, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ext_id, doc_id, chunk_ref, node_type, summary, 0.9, ext_metadata),
                )
                all_extractions.append(
                    {"type": node_type, "content": summary}
                )

            # Store claims
            for ci, claim in enumerate(data.get("claims", [])):
                if isinstance(claim, str) and claim.strip():
                    ext_id = f"{doc_id}:ext:claim:{batch_idx}:{ci}"
                    conn.conn.execute(
                        "INSERT OR REPLACE INTO extractions "
                        "(id, doc_id, chunk_index, node_type, content, confidence, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            ext_id,
                            doc_id,
                            chunk_ref,
                            "CLAIM",
                            claim.strip(),
                            0.85,
                            ext_metadata,
                        ),
                    )
                    all_extractions.append(
                        {"type": "CLAIM", "content": claim}
                    )

            # Store entities
            for ei, ent in enumerate(data.get("entities", [])):
                if isinstance(ent, dict) and ent.get("name"):
                    ent_id = f"{doc_id}:ent:{batch_idx}:{ei}"
                    desc = ent.get("description", "")
                    conn.conn.execute(
                        "INSERT OR REPLACE INTO entities "
                        "(id, doc_id, entity_type, entity_value, confidence) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            ent_id,
                            doc_id,
                            ent.get("type", "concept"),
                            f"{ent['name']}: {desc}" if desc else ent["name"],
                            0.85,
                        ),
                    )

            conn.conn.commit()
            logger.info(
                "Extraction batch %d/%d: %s + %d claims + %d entities",
                batch_idx + 1,
                len(batches),
                "DOCUMENT_SUMMARY" if is_first else "SECTION_SUMMARY",
                len(data.get("claims", [])),
                len(data.get("entities", [])),
            )

        conn.conn.commit()
        logger.info(
            "Document extraction complete: %s → %d extractions across %d batches",
            doc_id,
            len(all_extractions),
            len(batches),
        )
        return all_extractions

    # =====================================================================
    # Internal Helpers
    # =====================================================================

    def _get_conn(self, collection: str) -> AiBrarianConnection:
        if collection not in self.connections:
            available = list(self.connections.keys())
            raise ValueError(f"Collection '{collection}' not available. Connected: {available}")
        return self.connections[collection]

    def _get_generator(self, config: AiBrarianConfig) -> _EmbeddingGenerator:
        key = f"{config.embedding_model}:{config.embedding_dim}"
        if key not in self._generators:
            self._generators[key] = _EmbeddingGenerator(
                model=config.embedding_model, dim=config.embedding_dim
            )
        return self._generators[key]

    async def _embed_chunks(
        self,
        conn: AiBrarianConnection,
        doc_id: str,
        chunks: list[DocumentChunk],
    ) -> None:
        """Embed all chunks and store a document-level embedding (average).

        When sqlite-vec is loaded, writes to the chunk_embeddings vec0 table.
        When vec is unavailable but the pure-Python fallback is initialized,
        writes to chunk_embeddings_fallback via conn._fallback_vec.store().
        Returns silently if neither path is available.
        """
        fallback = getattr(conn, "_fallback_vec", None)
        if not conn._vec_loaded and not fallback:
            return

        gen = self._get_generator(conn.config)
        texts = [chunk.text for chunk in chunks]
        embeddings = await gen.generate_batch(texts)

        valid_embeddings = []
        for chunk, emb in zip(chunks, embeddings):
            if emb is None:
                continue
            chunk_id = f"{doc_id}:chunk:{chunk.index}"
            if conn._vec_loaded:
                blob = _vector_to_blob(emb)
                conn.conn.execute(
                    "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, blob),
                )
            else:
                fallback.store(chunk_id, emb)
            valid_embeddings.append(emb)

        # Document-level embedding = average of chunk embeddings
        if valid_embeddings:
            import numpy as np

            doc_vec = np.mean(valid_embeddings, axis=0).tolist()
            if conn._vec_loaded:
                blob = _vector_to_blob(doc_vec)
                conn.conn.execute(
                    "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                    (doc_id, blob),
                )
            else:
                fallback.store(doc_id, doc_vec)

    # =====================================================================
    # Cartridge Registration
    # =====================================================================

    async def register_cartridge(
        self,
        collection: str,
        lun_path: Path | str,
        metadata: Optional[dict] = None,
    ) -> str:
        """Register a .lun Knowledge Cartridge into an existing collection.

        Opens the .lun file, reads its nodes and embeddings, and inserts
        them as chunks into the collection's standard tables so existing
        search methods work without changes.

        Returns the doc_id assigned in the collection.
        """
        conn = self._get_conn(collection)
        if conn.config.read_only:
            raise PermissionError(f"Collection '{collection}' is read-only")

        lun_path = Path(lun_path)
        if not lun_path.exists():
            raise FileNotFoundError(f".lun file not found: {lun_path}")

        # Open .lun as read-only
        lun_conn = sqlite3.connect(f"file:{lun_path}?mode=ro", uri=True)
        lun_conn.row_factory = sqlite3.Row

        try:
            # Read meta
            meta_rows = lun_conn.execute("SELECT key, value FROM meta").fetchall()
            lun_meta = {row["key"]: row["value"] for row in meta_rows}
            title = lun_meta.get("title", lun_path.stem)
            word_count = int(lun_meta.get("word_count", "0"))

            # Gather full text for the documents table
            all_text = lun_conn.execute(
                "SELECT GROUP_CONCAT(content, ' ') FROM doc_nodes "
                "WHERE content IS NOT NULL AND type IN ('sentence', 'list_item', 'cell')"
            ).fetchone()[0] or ""

            # Create document record in collection
            doc_id = str(uuid.uuid4())
            coll_meta = metadata or {}
            coll_meta["lun_path"] = str(lun_path.resolve())
            coll_meta["cartridge"] = True

            conn.conn.execute(
                """INSERT OR REPLACE INTO documents
                   (id, filename, title, full_text, word_count, source_path,
                    doc_type, category, metadata, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'lun', ?, ?, CURRENT_TIMESTAMP)""",
                (
                    doc_id,
                    lun_path.name,
                    title,
                    all_text,
                    word_count,
                    str(lun_path.resolve()),
                    coll_meta.get("category"),
                    json.dumps(coll_meta),
                ),
            )

            # Map content-bearing nodes to chunks
            content_nodes = lun_conn.execute(
                """SELECT id, type, content, parent_id
                   FROM doc_nodes
                   WHERE content IS NOT NULL AND content != ''
                   AND type IN ('paragraph', 'sentence', 'list_item', 'cell')
                   ORDER BY id"""
            ).fetchall()

            # Build parent-to-section lookup for section_label
            section_map: dict[int, tuple[str, int]] = {}  # node_id -> (heading, level)
            sections = lun_conn.execute(
                "SELECT id, content, meta_json FROM doc_nodes WHERE type = 'section'"
            ).fetchall()
            for sec in sections:
                meta_j = json.loads(sec["meta_json"]) if sec["meta_json"] else {}
                section_map[sec["id"]] = (sec["content"] or "", meta_j.get("level", 1))

            # Walk parent chain to find section for each node
            def _find_section(node_id: int) -> tuple[str, int]:
                """Walk up parent chain until we hit a section node."""
                visited = set()
                current = node_id
                while current is not None and current not in visited:
                    visited.add(current)
                    if current in section_map:
                        return section_map[current]
                    row = lun_conn.execute(
                        "SELECT parent_id FROM doc_nodes WHERE id = ?", (current,)
                    ).fetchone()
                    current = row["parent_id"] if row else None
                return ("", 0)

            chunk_index = 0
            lun_node_to_chunk: dict[int, str] = {}  # lun node_id -> chunk_id

            for node in content_nodes:
                chunk_id = f"{doc_id}:chunk:{chunk_index}"
                section_label, section_level = _find_section(node["id"])

                conn.conn.execute(
                    """INSERT INTO chunks
                       (id, doc_id, chunk_index, chunk_text, word_count,
                        start_char, end_char, section_label, section_level)
                       VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        chunk_id,
                        doc_id,
                        chunk_index,
                        node["content"],
                        len(node["content"].split()),
                        section_label,
                        section_level,
                    ),
                )
                lun_node_to_chunk[node["id"]] = chunk_id
                chunk_index += 1

            # Persist section/paragraph-level embedded nodes as chunks too.
            # The .lun file's `embeddings` table is keyed by paragraph and
            # section node IDs whose `content` column is typically NULL —
            # the embedding text was concatenated from descendant sentences
            # at .lun creation time and never persisted at the parent. To
            # make :section:N embeddings resolvable at search time, walk
            # the doc_nodes tree once, build a parent->children adjacency
            # list, then for each embedded node not already a content chunk,
            # gather descendant text and insert it as a chunks row. This also
            # makes section headings/paragraphs FTS-searchable.
            all_nodes = lun_conn.execute(
                "SELECT id, parent_id, content FROM doc_nodes"
            ).fetchall()
            children_of: dict[int, list[int]] = {}
            content_of: dict[int, str] = {}
            for n in all_nodes:
                pid = n["parent_id"]
                if pid is not None:
                    children_of.setdefault(pid, []).append(n["id"])
                if n["content"]:
                    content_of[n["id"]] = n["content"]

            def _gather_subtree_text(root_id: int) -> str:
                parts: list[str] = []
                stack = [root_id]
                while stack:
                    nid = stack.pop()
                    if nid in content_of:
                        parts.append(content_of[nid])
                    if nid in children_of:
                        stack.extend(children_of[nid])
                return " ".join(parts).strip()

            embedded_node_ids = [
                row["node_id"]
                for row in lun_conn.execute(
                    "SELECT node_id FROM embeddings"
                ).fetchall()
            ]
            for emb_node_id in embedded_node_ids:
                if emb_node_id in lun_node_to_chunk:
                    continue  # already persisted as a content chunk
                text = _gather_subtree_text(emb_node_id)
                if not text:
                    continue
                section_label, section_level = section_map.get(emb_node_id, ("", 0))
                section_chunk_id = f"{doc_id}:section:{emb_node_id}"
                conn.conn.execute(
                    """INSERT INTO chunks
                       (id, doc_id, chunk_index, chunk_text, word_count,
                        start_char, end_char, section_label, section_level)
                       VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        section_chunk_id,
                        doc_id,
                        chunk_index,
                        text,
                        len(text.split()),
                        section_label,
                        section_level,
                    ),
                )
                lun_node_to_chunk[emb_node_id] = section_chunk_id
                chunk_index += 1

            # Copy embeddings to collection's chunk_embeddings (or fallback)
            fallback = getattr(conn, "_fallback_vec", None)
            if conn._vec_loaded or fallback:
                embeddings = lun_conn.execute(
                    "SELECT node_id, level, vector FROM embeddings"
                ).fetchall()

                valid_embeddings = []
                for emb_row in embeddings:
                    node_id = emb_row["node_id"]
                    chunk_id = lun_node_to_chunk.get(node_id)
                    if not chunk_id:
                        # Section-level embedding — map to a synthetic chunk_id
                        chunk_id = f"{doc_id}:section:{node_id}"
                    blob = emb_row["vector"]
                    if conn._vec_loaded:
                        conn.conn.execute(
                            "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                            (chunk_id, blob),
                        )
                    else:
                        fallback.store(chunk_id, blob)
                    valid_embeddings.append(blob)

                # Doc-level average embedding
                if valid_embeddings:
                    import numpy as np
                    vecs = [_blob_to_vector(b) for b in valid_embeddings]
                    doc_vec = np.mean(vecs, axis=0).tolist()
                    if conn._vec_loaded:
                        conn.conn.execute(
                            "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                            (doc_id, _vector_to_blob(doc_vec)),
                        )
                    else:
                        fallback.store(doc_id, _vector_to_blob(doc_vec))

            conn.conn.commit()
            logger.info(
                "Registered cartridge %s into '%s' — %d chunks, doc_id=%s",
                lun_path.name, collection, chunk_index, doc_id,
            )
            return doc_id

        finally:
            lun_conn.close()
