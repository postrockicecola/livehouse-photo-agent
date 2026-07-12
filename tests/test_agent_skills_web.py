"""Tests for the general-agent web + artifact skills (offline, injected transports)."""
from __future__ import annotations

import os

from services.agent.skills import general_registry, safe_session_id
from services.agent.skills.artifacts import WriteArtifactSkill, sanitize_artifact_name
from services.agent.skills.web import (
    WebFetchSkill,
    WebSearchSkill,
    _strip_html_to_text,
)


# --------------------------------------------------------------------- web_search


def test_web_search_formats_injected_results():
    skill = WebSearchSkill(
        searcher=lambda q, n: [
            {"title": "Result A", "url": "https://a.example", "snippet": "about a"},
            {"title": "Result B", "url": "https://b.example", "snippet": "about b"},
        ]
    )
    res = skill.run({"query": "anything", "max_results": 5})
    assert res.ok
    assert "Result A" in res.output and "https://a.example" in res.output
    assert res.metadata["results"][1]["url"] == "https://b.example"


def test_web_search_requires_query():
    assert WebSearchSkill(searcher=lambda q, n: []).run({"query": "  "}).ok is False


def test_web_search_empty_results_is_ok():
    res = WebSearchSkill(searcher=lambda q, n: []).run({"query": "obscure"})
    assert res.ok and res.metadata["results"] == []


def test_web_search_degrades_on_searcher_error():
    def boom(q, n):
        raise RuntimeError("network down")

    res = WebSearchSkill(searcher=boom).run({"query": "x"})
    assert res.ok is False and "network down" in (res.error or "")


# ---------------------------------------------------------------------- web_fetch


def test_web_fetch_strips_html_and_truncates():
    html_body = "<html><head><style>x{}</style></head><body><h1>Hi</h1><p>World &amp; co</p><script>bad()</script></body></html>"
    skill = WebFetchSkill(fetcher=lambda url: (200, html_body), text_cap=1000)
    res = skill.run({"url": "https://example.com"})
    assert res.ok
    assert "Hi" in res.output and "World & co" in res.output
    assert "bad()" not in res.output and "<" not in res.output


def test_web_fetch_rejects_non_http_url():
    assert WebFetchSkill(fetcher=lambda u: (200, "")).run({"url": "ftp://x"}).ok is False


def test_web_fetch_reports_http_error_status():
    res = WebFetchSkill(fetcher=lambda u: (404, "nope")).run({"url": "https://x.example"})
    assert res.ok is False and res.metadata.get("status") == 404


def test_web_fetch_truncates_over_cap():
    big = "<p>" + ("A" * 5000) + "</p>"
    res = WebFetchSkill(fetcher=lambda u: (200, big), text_cap=100).run({"url": "https://x.example"})
    assert res.metadata["truncated"] is True and "truncated" in res.output


def test_strip_html_collapses_whitespace():
    assert _strip_html_to_text("<div>a</div>\n\n\n\n<div>b</div>") == "a\n\nb"


# ------------------------------------------------------------------- write_artifact


def test_write_artifact_persists_and_sanitizes(tmp_path):
    skill = WriteArtifactSkill(str(tmp_path), url_prefix="/api/agent/artifacts/s1")
    res = skill.run({"name": "../../etc/pa ss wd", "content": "hello"})
    assert res.ok
    # Name is confined to a safe basename inside the session dir.
    written = os.listdir(tmp_path)
    assert len(written) == 1
    assert res.metadata["url"].startswith("/api/agent/artifacts/s1/")
    assert res.metadata["bytes"] == 5


def test_write_artifact_rejects_empty_and_oversized(tmp_path):
    skill = WriteArtifactSkill(str(tmp_path), url_prefix="/p")
    assert skill.run({"name": "a.txt", "content": ""}).ok is False
    assert skill.run({"name": "a.txt", "content": "x" * 2_000_000}).ok is False


def test_sanitize_artifact_name_adds_extension():
    assert sanitize_artifact_name("report").endswith(".txt")
    assert sanitize_artifact_name("data.json") == "data.json"
    assert "/" not in sanitize_artifact_name("../../x.md")


# --------------------------------------------------------------------- registry


def test_general_registry_has_expected_tools():
    reg = general_registry("gallery-xyz")
    assert set(reg.names()) == {"web_search", "web_fetch", "python_exec", "write_artifact"}


def test_safe_session_id_is_single_segment():
    assert "/" not in safe_session_id("a/b/../c")
    assert safe_session_id("") == "default"
