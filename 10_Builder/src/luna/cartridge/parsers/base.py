"""Base parser interface for document-to-node-tree conversion."""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseParser(ABC):
    """Abstract base for source format parsers.

    Parsers return a flat list of node dicts.  Each dict has:
        type:       str        – node type (document, section, paragraph, …)
        content:    str | None – text content (None for container nodes)
        parent_idx: int | None – index into the returned list of the parent node
        position:   int        – sibling order under parent
        meta:       dict | None – optional format-specific extras
    """

    @abstractmethod
    def parse(self, source_path: Path) -> list[dict]:
        ...
