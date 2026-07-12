"""Web skills: let a general-purpose agent search and read the open web.

Two read-only skills, both network-bound and **degrade gracefully** (a failed fetch
comes back as a failed :class:`SkillResult`, never an exception that kills the loop):

- ``web_search`` — top-N results (title / url / snippet) for a query.
- ``web_fetch``  — fetch one URL and return readable, tag-stripped text (truncated).

Both are constructed with an injectable callable (``searcher`` / ``fetcher``) so unit
tests run offline with canned data and production wires the default ``requests`` path.
No API key is required: search uses the DuckDuckGo HTML endpoint.
"""
from __future__ import annotations

import html
import re
from typing import Any, Callable, Optional
from urllib.parse import unquote

from services.agent.skills.base import SkillResult

# searcher(query, max_results) -> list[{"title","url","snippet"}]
Searcher = Callable[[str, int], list[dict[str, str]]]
# fetcher(url) -> (status_code, body_text)
Fetcher = Callable[[str], "tuple[int, str]"]

_USER_AGENT = "Mozilla/5.0 (compatible; LivehouseAgent/1.0; +https://example.com/bot)"
_TEXT_CAP = 6000  # chars of extracted page text kept in the observation
_DEFAULT_TIMEOUT = 15


def _strip_html_to_text(body: str) -> str:
    """Best-effort HTML → readable text: drop script/style, unwrap tags, unescape."""
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r" *\n *", "\n", body)  # trim spaces hugging newlines
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _ddg_search(query: str, max_results: int) -> list[dict[str, str]]:
    """Default searcher: DuckDuckGo HTML endpoint (no API key). Parsed with regex."""
    import requests

    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": _USER_AGENT},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    results: list[dict[str, str]] = []
    # Each result: <a class="result__a" href="...">title</a> ... <a class="result__snippet">snippet</a>
    for m in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        resp.text,
        re.IGNORECASE | re.DOTALL,
    ):
        url = html.unescape(m.group(1))
        # DDG wraps some links as /l/?uddg=<encoded>; unwrap when present.
        uddg = re.search(r"[?&]uddg=([^&]+)", url)
        if uddg:
            url = unquote(uddg.group(1))
        title = _strip_html_to_text(m.group(2))
        results.append({"title": title, "url": url, "snippet": ""})
        if len(results) >= max_results:
            break
    snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.IGNORECASE | re.DOTALL
    )
    for i, snip in enumerate(snippets[: len(results)]):
        results[i]["snippet"] = _strip_html_to_text(snip)[:280]
    return results


def _requests_fetch(url: str) -> "tuple[int, str]":
    import requests

    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_DEFAULT_TIMEOUT)
    return resp.status_code, resp.text


class WebSearchSkill:
    """Search the web and return the top results (title / url / snippet)."""

    name = "web_search"
    description = (
        "Search the public web for a query and return the top results as a list of "
        "{title, url, snippet}. Use this to find sources before reading them with web_fetch."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10,
                            "description": "How many results to return (default 5)."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, *, searcher: Optional[Searcher] = None, default_max: int = 5) -> None:
        self._search = searcher or _ddg_search
        self._default_max = default_max

    def run(self, args: dict[str, Any]) -> SkillResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return SkillResult(ok=False, error="'query' must be a non-empty string")
        try:
            n = int(args.get("max_results") or self._default_max)
        except (TypeError, ValueError):
            n = self._default_max
        n = max(1, min(10, n))
        try:
            results = self._search(query, n)
        except Exception as exc:  # network / parse errors → degrade, don't crash the loop
            return SkillResult(ok=False, error=f"search failed: {exc}")
        if not results:
            return SkillResult(ok=True, output=f"No results for {query!r}.", metadata={"results": []})
        lines = [f"{i + 1}. {r['title']} — {r['url']}\n   {r.get('snippet', '')}".rstrip()
                 for i, r in enumerate(results)]
        return SkillResult(ok=True, output="\n".join(lines), metadata={"results": results})


class WebFetchSkill:
    """Fetch one URL and return its readable text content (HTML stripped, truncated)."""

    name = "web_fetch"
    description = (
        "Fetch a single http(s) URL and return its main text content with HTML stripped "
        "(truncated). Use after web_search to read a specific page."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to fetch."},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, *, fetcher: Optional[Fetcher] = None, text_cap: int = _TEXT_CAP) -> None:
        self._fetch = fetcher or _requests_fetch
        self._cap = text_cap

    def run(self, args: dict[str, Any]) -> SkillResult:
        url = str(args.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return SkillResult(ok=False, error="'url' must be an absolute http(s) URL")
        try:
            status, body = self._fetch(url)
        except Exception as exc:
            return SkillResult(ok=False, error=f"fetch failed: {exc}")
        if status >= 400:
            return SkillResult(ok=False, error=f"fetch returned HTTP {status}", metadata={"status": status})
        text = _strip_html_to_text(body or "")
        truncated = len(text) > self._cap
        if truncated:
            text = text[: self._cap] + f"\n...[truncated {len(text) - self._cap} chars]"
        return SkillResult(
            ok=True,
            output=text or "(empty page)",
            metadata={"status": status, "url": url, "truncated": truncated},
        )
