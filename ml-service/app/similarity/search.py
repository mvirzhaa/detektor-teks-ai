"""
Penyedia pencarian web untuk menemukan halaman internet yang mungkin menjadi sumber salinan.

  SEARCH_PROVIDER=brave       → Brave Search API (BRAVE_API_KEY)
  SEARCH_PROVIDER=serpapi     → SerpAPI / Google (SERPAPI_KEY)
  SEARCH_PROVIDER=google_cse  → Google Custom Search JSON API (GOOGLE_CSE_KEY, GOOGLE_CSE_CX)
                                 catatan: tertutup untuk pelanggan baru & dijadwalkan berhenti 1 Jan 2027
  SEARCH_PROVIDER=searxng     → SearXNG self-hosted (SEARXNG_URL), gratis; format JSON harus aktif
  SEARCH_PROVIDER=none        → pencarian web dimatikan (hanya repositori/indeks lokal)
"""
from __future__ import annotations

import logging

import httpx

from . import config as C

log = logging.getLogger("sim.search")


class SearchError(Exception):
    pass


def provider_name() -> str:
    return C.SEARCH_PROVIDER


def is_configured() -> bool:
    p = C.SEARCH_PROVIDER
    return (p == "brave" and bool(C.BRAVE_API_KEY)) or (p == "serpapi" and bool(C.SERPAPI_KEY)) or \
        (p == "google_cse" and bool(C.GOOGLE_CSE_KEY and C.GOOGLE_CSE_CX)) or (p == "searxng" and bool(C.SEARXNG_URL))


def search(query: str, count: int) -> list[dict]:
    """Return [{url, title, snippet}]"""
    p = C.SEARCH_PROVIDER
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": C.USER_AGENT}) as h:
            if p == "brave":
                r = h.get("https://api.search.brave.com/res/v1/web/search",
                          params={"q": query, "count": min(count, 20), "search_lang": C.SEARCH_LANG},
                          headers={"X-Subscription-Token": C.BRAVE_API_KEY, "Accept": "application/json"})
                r.raise_for_status()
                items = (r.json().get("web") or {}).get("results") or []
                return [{"url": i.get("url"), "title": i.get("title"), "snippet": i.get("description")} for i in items]
            if p == "serpapi":
                r = h.get("https://serpapi.com/search.json",
                          params={"engine": "google", "q": query, "num": count, "hl": C.SEARCH_LANG,
                                  "api_key": C.SERPAPI_KEY})
                r.raise_for_status()
                items = r.json().get("organic_results") or []
                return [{"url": i.get("link"), "title": i.get("title"), "snippet": i.get("snippet")} for i in items]
            if p == "google_cse":
                r = h.get("https://www.googleapis.com/customsearch/v1",
                          params={"key": C.GOOGLE_CSE_KEY, "cx": C.GOOGLE_CSE_CX, "q": query, "num": min(count, 10)})
                r.raise_for_status()
                items = r.json().get("items") or []
                return [{"url": i.get("link"), "title": i.get("title"), "snippet": i.get("snippet")} for i in items]
            if p == "searxng":
                r = h.get(f"{C.SEARXNG_URL}/search", params={"q": query, "format": "json", "language": C.SEARCH_LANG})
                r.raise_for_status()
                items = r.json().get("results") or []
                return [{"url": i.get("url"), "title": i.get("title"), "snippet": i.get("content")}
                        for i in items[:count]]
    except httpx.HTTPStatusError as e:
        raise SearchError(f"{p}: HTTP {e.response.status_code}") from e
    except (httpx.HTTPError, ValueError) as e:
        raise SearchError(f"{p}: {e}") from e
    return []
