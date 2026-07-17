"""
Per-Project Search Chain Configuration & Runner.

Loads project-specific YAML configs from config/projects/{slug}.yaml
and walks an ordered search chain to pre-fetch knowledge for the voice path.

Follows the FallbackConfig pattern (src/luna/llm/fallback_config.py).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Dict, Optional

from luna.core.paths import config_dir

logger = logging.getLogger(__name__)

# Config directory: <project_root>/config/projects/
_CONFIG_DIR = config_dir() / "projects"


def _default_sources() -> list["SearchSourceConfig"]:
    """Default search chain: Memory Matrix + AiBrarian dataroom."""
    return [
        SearchSourceConfig(type="matrix", max_tokens=2500),
        SearchSourceConfig(type="dataroom", max_tokens=2500, limit=3),
    ]


@dataclass
class SearchSourceConfig:
    """Configuration for a single search source in the chain."""

    type: str                                  # matrix | dataroom | local_files | web_search
    max_tokens: int = 1500
    limit: int = 3
    paths: list[str] = field(default_factory=list)           # local_files only
    patterns: list[str] = field(default_factory=lambda: ["*.md", "*.txt", "*.yaml"])  # local_files only
    category: Optional[str] = None             # dataroom only


@dataclass
class SearchChainConfig:
    """
    Full search chain configuration for a project.

    Loaded from config/projects/{slug}.yaml or built-in defaults.
    """

    max_total_tokens: int = 5000
    sources: list[SearchSourceConfig] = field(default_factory=_default_sources)

    @classmethod
    def default(cls) -> "SearchChainConfig":
        """Returns the default config (matrix + dataroom)."""
        return cls()

    @classmethod
    def load(cls, slug: str, config_dir: Optional[Path] = None) -> "SearchChainConfig":
        """
        Load from config/projects/{slug}.yaml, fall back to defaults.

        Args:
            slug: Project slug (used as filename)
            config_dir: Override config directory (for testing)

        Returns:
            SearchChainConfig instance
        """
        base_dir = config_dir or _CONFIG_DIR
        config_path = base_dir / f"{slug}.yaml"

        if not config_path.exists():
            logger.info(f"No project search config for '{slug}', using defaults")
            return cls.default()

        try:
            import yaml

            with open(config_path, "r") as f:
                data = yaml.safe_load(f) or {}

            chain_data = data.get("search_chain", {})
            max_total = chain_data.get("max_total_tokens", 3000)

            sources = []
            for src in chain_data.get("sources", []):
                sources.append(SearchSourceConfig(
                    type=src.get("type", "matrix"),
                    max_tokens=src.get("max_tokens", 1500),
                    limit=src.get("limit", 3),
                    paths=src.get("paths", []),
                    patterns=src.get("patterns", ["*.md", "*.txt", "*.yaml"]),
                    category=src.get("category"),
                ))

            if not sources:
                logger.warning(f"No sources in {config_path}, using defaults")
                return cls.default()

            config = cls(max_total_tokens=max_total, sources=sources)
            source_types = [s.type for s in sources]
            logger.info(f"Loaded search chain for '{slug}': {source_types} (budget={max_total})")
            return config

        except ImportError:
            logger.warning("PyYAML not installed, using default search config")
            return cls.default()
        except Exception as e:
            logger.error(f"Failed to load search config for '{slug}': {e}")
            return cls.default()


async def run_search_chain(
    config: SearchChainConfig,
    query: str,
    engine: Any,
) -> List[Dict]:
    """
    Walk the search chain, accumulate results, respect token budgets.

    Args:
        config: Search chain configuration
        query: User's query
        engine: LunaEngine instance (for actor access)

    Returns:
        List[Dict] with keys: content, node_type, source
    """
    results: List[Dict] = []
    tokens_used = 0

    for source in config.sources:
        remaining = config.max_total_tokens - tokens_used
        if remaining < 50:
            logger.info(
                f"[SEARCH-CHAIN] Budget exhausted ({tokens_used}/{config.max_total_tokens}), stopping"
            )
            break

        effective_max = min(source.max_tokens, remaining)

        try:
            source_results = await _dispatch_source(source, query, engine, effective_max)
        except Exception as e:
            logger.warning(f"[SEARCH-CHAIN] Source '{source.type}' failed: {e}")
            continue

        for item in source_results:
            content = item.get("content", "")
            item_tokens = len(content) // 4

            if tokens_used + item_tokens > config.max_total_tokens:
                chars_remaining = (config.max_total_tokens - tokens_used) * 4
                if chars_remaining > 100:
                    item["content"] = content[:chars_remaining] + "\n[...truncated]"
                    item_tokens = chars_remaining // 4
                else:
                    break

            results.append(item)
            tokens_used += item_tokens

    logger.info(f"[SEARCH-CHAIN] Returned {len(results)} items, ~{tokens_used} tokens")
    return results


async def _dispatch_source(
    source: SearchSourceConfig,
    query: str,
    engine: Any,
    max_tokens: int,
) -> List[Dict]:
    """Route to the appropriate source handler."""
    if source.type == "matrix":
        return await _search_matrix(query, engine, max_tokens)
    elif source.type == "dataroom":
        return await _search_dataroom(query, source.limit, source.category)
    elif source.type == "local_files":
        return await _search_local_files(query, source.paths, source.patterns, source.limit)
    elif source.type == "web_search":
        return await _search_web(query, source.limit, max_tokens * 4)
    else:
        logger.warning(f"[SEARCH-CHAIN] Unknown source type: {source.type}")
        return []


async def _search_matrix(query: str, engine: Any, max_tokens: int) -> List[Dict]:
    """Search Memory Matrix via Matrix actor."""
    matrix = engine.get_actor("matrix") if hasattr(engine, 'get_actor') else None
    if not matrix:
        logger.error("[SEARCH-CHAIN] Matrix actor not found — memory search DISABLED")
        return []

    # Wait for Matrix readiness (up to 3s) to handle startup race
    if not getattr(matrix, 'is_ready', False):
        logger.warning("[SEARCH-CHAIN] Matrix not ready, waiting up to 3s...")
        for i in range(30):
            await asyncio.sleep(0.1)
            if getattr(matrix, 'is_ready', False):
                logger.info(f"[SEARCH-CHAIN] Matrix ready after {(i + 1) * 100}ms")
                break
        else:
            logger.error("[SEARCH-CHAIN] Matrix failed to initialize within 3s — memory search DISABLED")
            return []

    memory_text = await matrix.get_context(query, max_tokens=max_tokens)
    if not memory_text:
        return []

    logger.info(f"[SEARCH-CHAIN] matrix: {len(memory_text)} chars")
    return [{
        "content": memory_text,
        "node_type": "memory_context",
        "source": "matrix",
    }]


async def _search_dataroom(
    query: str, limit: int, category: Optional[str] = None
) -> List[Dict]:
    """Search AiBrarian dataroom."""
    try:
        from luna.tools.dataroom_tools import dataroom_search
    except ImportError:
        logger.warning("[SEARCH-CHAIN] dataroom: import failed")
        return []

    try:
        docs = await dataroom_search(query=query, category=category, limit=limit)
    except Exception as e:
        logger.warning(f"[SEARCH-CHAIN] dataroom search exception: {e}")
        return []

    if not docs:
        return []

    # Check for error entries
    if any(d.get("error") for d in docs):
        logger.warning(f"[SEARCH-CHAIN] dataroom: error in results: {[d.get('error') for d in docs if d.get('error')]}")
        return []

    results = []
    for doc in docs:
        content = doc.get("content") or doc.get("snippet", "")
        title = doc.get("name", "") or doc.get("title", "")
        if content:
            results.append({
                "content": f"[Dataroom: {title}]\n{content}" if title else content,
                "node_type": "dataroom",
                "source": "aibrarian",
            })

    logger.info(f"[SEARCH-CHAIN] dataroom: {len(results)} docs, {sum(len(r['content']) for r in results)} chars")
    return results


async def _search_local_files(
    query: str, paths: list, patterns: list, limit: int
) -> List[Dict]:
    """Search local project files."""
    try:
        from luna.tools.local_file_search import local_file_search
    except ImportError:
        return []

    files = await local_file_search(
        query=query, paths=paths or None, patterns=patterns or None, limit=limit
    )
    results = []
    for f in files:
        content = f.get("content", "")
        file_path = f.get("file_path", "")
        if content:
            results.append({
                "content": f"[File: {file_path}]\n{content}" if file_path else content,
                "node_type": "local_file",
                "source": "local_files",
            })

    if results:
        logger.info(f"[SEARCH-CHAIN] local_files: {len(results)} files")
    return results


async def _search_web(query: str, limit: int, max_chars: int) -> List[Dict]:
    """Search the web via Tavily API."""
    try:
        from luna.tools.web_search import web_search
    except ImportError:
        return []

    web_results = await web_search(query=query, limit=limit, max_chars=max_chars)
    results = []
    for wr in web_results:
        content = wr.get("content", "")
        title = wr.get("title", "")
        url = wr.get("url", "")
        if content:
            header = f"[Web: {title}]" if title else "[Web]"
            if url:
                header += f" ({url})"
            results.append({
                "content": f"{header}\n{content}",
                "node_type": "web_result",
                "source": "web_search",
            })

    if results:
        logger.info(f"[SEARCH-CHAIN] web_search: {len(results)} results")
    return results
