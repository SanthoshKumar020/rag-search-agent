"""Web search — free, no API key needed.

Transport chain (first one that returns results wins):
  1. DuckDuckGo via the `ddgs` package (primary).
  2. DuckDuckGo lite HTML endpoint via `requests`.
  3. Public SearXNG instances (JSON API) via `requests`.
  4. Bing HTML via `requests` (redirect URLs decoded).

Search engines sometimes bot-wall datacenter IPs; the chain makes the
agent resilient to that. `requests` honours the standard *_proxy
environment variables, which the ddgs HTTP client does not always do.
"""
import base64
import os
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
LITE_URL = "https://lite.duckduckgo.com/lite/"
SEARXNG_INSTANCES = [
    "https://search.inetol.net/search",
    "https://searx.be/search",
    "https://search.rhscz.eu/search",
    "https://baresearch.org/search",
]
BING_URL = "https://www.bing.com/search"


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Return a list of {title, url, snippet} dicts for the query."""
    for transport in (_search_ddgs, _search_lite, _search_searxng,
                      _search_bing):
        results = transport(query, max_results)
        if results:
            return results
    return []


def _search_ddgs(query: str, max_results: int) -> list[dict]:
    proxy = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
             or os.environ.get("http_proxy") or None)
    out: list[dict] = []
    try:
        with DDGS(proxy=proxy, timeout=25) as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                out.append(
                    {
                        "title": (r.get("title") or "").strip(),
                        "url": url,
                        "snippet": (r.get("body") or "").strip(),
                    }
                )
    except Exception:
        return []
    return out


def _search_lite(query: str, max_results: int) -> list[dict]:
    try:
        resp = requests.get(LITE_URL, params={"q": query},
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out: list[dict] = []
    current = None
    for row in soup.select("tr"):
        link = row.select_one("a.result-link")
        if link and link.get("href"):
            if current:
                out.append(current)
            current = {"title": link.get_text(strip=True),
                       "url": link["href"], "snippet": ""}
            continue
        cell = row.select_one("td.result-snippet")
        if cell and current is not None:
            current["snippet"] = cell.get_text(" ", strip=True)
            out.append(current)
            current = None
        if len(out) >= max_results:
            break
    if current and len(out) < max_results:
        out.append(current)
    return out[:max_results]


def _search_searxng(query: str, max_results: int) -> list[dict]:
    for instance in SEARXNG_INSTANCES:
        try:
            resp = requests.get(
                instance,
                params={"q": query, "format": "json"},
                headers=HEADERS,
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue
        out = []
        for r in data.get("results", [])[:max_results]:
            if r.get("url"):
                out.append(
                    {
                        "title": (r.get("title") or "").strip(),
                        "url": r["url"],
                        "snippet": (r.get("content") or "").strip(),
                    }
                )
        if out:
            return out
    return []


def _bing_decode(url: str) -> str:
    """Bing wraps result URLs in a redirect with a base64 target."""
    try:
        qs = parse_qs(urlparse(url).query)
        if "u" in qs:
            v = qs["u"][0]
            if v.startswith("a1"):
                v = v[2:]
            return base64.b64decode(v + "=" * (-len(v) % 4)).decode(
                "utf-8", "ignore"
            )
    except Exception:
        pass
    return url


def _search_bing(query: str, max_results: int) -> list[dict]:
    try:
        resp = requests.get(BING_URL, params={"q": query},
                            headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out: list[dict] = []
    for item in soup.select("li.b_algo")[:max_results]:
        link = item.select_one("h2 a")
        if not link or not link.get("href"):
            continue
        snippet = item.select_one(".b_caption p")
        out.append(
            {
                "title": link.get_text(" ", strip=True),
                "url": _bing_decode(link["href"]),
                "snippet": snippet.get_text(" ", strip=True)
                if snippet else "",
            }
        )
    return out
