"""
WebSearch.py
Uses Tavily API — built for AI agents, reliable, free tier 1000 searches/month.
Never blocks bots, returns clean summarized results.

Get free API key at: https://tavily.com
Set env var: TAVILY_API_KEY=tvly-...
"""

import os
import httpx
from typing import Optional


class WebSearch:
    def __init__(self):
        self.api_key = os.environ.get("TAVILY_API_KEY", "")
        self._client = httpx.AsyncClient(timeout=5.0)

        if not self.api_key:
            print("[WebSearch] ⚠️  TAVILY_API_KEY not set — web search disabled")

    async def search(self, query: str, max_results: int = 3) -> Optional[str]:
        """
        Search the web via Tavily and return a short summary string.
        Returns None if search fails or API key not set.
        """
        if not self.api_key:
            return None

        try:
            response = await self._client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key":              self.api_key,
                    "query":                query,
                    "search_depth":         "basic",   # basic = faster, advanced = deeper
                    "max_results":          max_results,
                    "include_answer":       True,       # Tavily provides a direct answer
                    "include_raw_content":  False,
                },
            )
            response.raise_for_status()
            data = response.json()

            # Tavily provides a direct summarized answer — perfect for TTS
            answer = data.get("answer", "").strip()
            if answer:
                print(f"[WebSearch] Got answer: {answer[:100]}...")
                return answer

            # Fallback to top results if no direct answer
            results = data.get("results", [])
            parts = []
            for r in results[:max_results]:
                content = r.get("content", "").strip()
                if content:
                    parts.append(content[:200])

            if parts:
                return " ".join(parts)[:500]

            return None

        except Exception as e:
            print(f"[WebSearch] Tavily search failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()