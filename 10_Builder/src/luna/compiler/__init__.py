"""
Knowledge Compiler
==================

Batch compilation of source documents into 6D-classified Memory Matrix
nodes with typed graph edges and entity constellations.

Phases:
  1-6: Core compilation (compiler.py)
  1b:  Conversation extraction (conversation_extractor.py)
  1c:  Markdown archive export (markdown_export.py)
"""

from .compiler import CompileResult, KnowledgeCompiler
from .constellation_prefetch import ConstellationPrefetch, PrefetchResult
from .entity_index import EntityIndex, EntityProfile
from .conversation_extractor import ConversationExtractor, ExtractResult
from .markdown_export import MarkdownExporter, ExportResult

__all__ = [
    "KnowledgeCompiler",
    "CompileResult",
    "ConstellationPrefetch",
    "PrefetchResult",
    "EntityIndex",
    "EntityProfile",
    "ConversationExtractor",
    "ExtractResult",
    "MarkdownExporter",
    "ExportResult",
]
