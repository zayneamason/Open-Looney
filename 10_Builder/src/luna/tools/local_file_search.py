"""
Local file search source for Luna Engine search chain.

Searches project files by content matching (case-insensitive substring).
"""

import asyncio
import logging
from functools import partial
from typing import Optional

from luna.core.paths import project_root

logger = logging.getLogger(__name__)

# Project root for resolving relative paths
_PROJECT_ROOT = project_root()


def _sync_search_files(
    query: str,
    paths: list[str],
    patterns: list[str],
    max_results: int,
) -> list[dict]:
    """
    Synchronous file content search (runs in executor).

    Simple case-insensitive substring matching.
    Returns snippets around match locations.
    """
    results = []
    query_lower = query.lower()
    terms = [t.strip() for t in query_lower.split() if len(t.strip()) > 2]
    if not terms:
        return []

    for search_path in paths:
        resolved = Path(search_path)
        if not resolved.is_absolute():
            resolved = _PROJECT_ROOT / search_path
        if not resolved.exists() or not resolved.is_dir():
            continue

        for pattern in patterns:
            for file_path in resolved.rglob(pattern):
                if not file_path.is_file():
                    continue
                try:
                    if file_path.stat().st_size > 512_000:
                        continue
                except OSError:
                    continue

                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                text_lower = text.lower()
                score = sum(1 for t in terms if t in text_lower)
                if score == 0:
                    continue

                # Extract snippet around first match
                first_term = next((t for t in terms if t in text_lower), terms[0])
                idx = text_lower.find(first_term)
                start = max(0, idx - 200)
                end = min(len(text), idx + len(first_term) + 600)
                snippet = text[start:end].strip()

                try:
                    rel_path = file_path.relative_to(_PROJECT_ROOT)
                except ValueError:
                    rel_path = file_path

                results.append({
                    "content": snippet,
                    "file_path": str(rel_path),
                    "score": score,
                })

                if len(results) >= max_results * 3:
                    break
            if len(results) >= max_results * 3:
                break
        if len(results) >= max_results * 3:
            break

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


async def local_file_search(
    query: str,
    paths: Optional[list[str]] = None,
    patterns: Optional[list[str]] = None,
    limit: int = 5,
) -> list[dict]:
    """
    Search local project files for content matching a query.

    Args:
        query: Search text
        paths: Directories to search (relative to project root)
        patterns: Glob patterns for files to include
        limit: Max results

    Returns:
        List of dicts with keys: content, file_path, score
    """
    search_paths = paths or ["."]
    search_patterns = patterns or ["*.md", "*.txt", "*.yaml", "*.py"]

    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(
        None,
        partial(
            _sync_search_files,
            query,
            search_paths,
            search_patterns,
            limit,
        ),
    )

    logger.info(f"[LOCAL-SEARCH] Returned {len(results)} results for '{query[:40]}'")
    return results
