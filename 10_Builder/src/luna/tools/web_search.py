"""
Web search source for Luna Engine search chain.

Uses Tavily API for grounded web search results.
API key via TAVILY_API_KEY env var.
"""

import os
import logging

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")


async def web_search(
    query: str,
    limit: int = 3,
    max_chars: int = 4000,
) -> list[dict]:
    """
    Search the web via Tavily API.

    Args:
        query: Search query
        limit: Max results
        max_chars: Max total characters to return

    Returns:
        List of dicts with keys: content, title, url
    """
    if not TAVILY_API_KEY:
        logger.debug("[WEB-SEARCH] TAVILY_API_KEY not set, skipping")
        return []

    try:
        import httpx
    except ImportError:
        logger.warning("[WEB-SEARCH] httpx not installed, skipping")
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": limit,
                    "include_answer": True,
                    "search_depth": "basic",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        total_chars = 0

        # Include the AI-generated answer summary if available
        answer = data.get("answer", "")
        if answer:
            results.append({
                "content": answer,
                "title": "Web Summary",
                "url": "",
            })
            total_chars += len(answer)

        # Include individual search results
        for item in data.get("results", []):
            content = item.get("content", "")
            if total_chars + len(content) > max_chars:
                content = content[:max_chars - total_chars]
            if not content:
                break
            results.append({
                "content": content,
                "title": item.get("title", ""),
                "url": item.get("url", ""),
            })
            total_chars += len(content)
            if total_chars >= max_chars:
                break

        logger.info(f"[WEB-SEARCH] Returned {len(results)} results ({total_chars} chars)")
        return results

    except Exception as e:
        logger.warning(f"[WEB-SEARCH] Failed: {e}")
        return []
