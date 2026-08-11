from __future__ import annotations

from services.agent.skills.knowledge_search import KnowledgeSearchSkill
from services.knowledge_index import build_knowledge_index, tokenize


def test_knowledge_search_chunks_and_cites_sources(tmp_path) -> None:
    (tmp_path / "xiaohongshu.md").write_text(
        """---
title: 小红书发布规范
tenant: default
allowed_owners: ["*"]
source_url: https://example.test/xhs
---
小红书人像发布建议优先使用竖构图，并保留主体周围的安全区域。

导出前需要检查曝光、主体清晰度和裁切位置。
""",
        encoding="utf-8",
    )
    build_knowledge_index(tmp_path)
    result = KnowledgeSearchSkill(
        str(tmp_path),
        owner="anon:user",
    ).run({"query": "小红书竖构图安全区域", "mode": "text"})

    assert result.ok
    assert result.metadata["chunks"]
    first = result.metadata["chunks"][0]
    assert "竖构图" in first["text"]
    assert first["source_ref"].startswith("xiaohongshu.md#chunk-")
    assert result.metadata["sources"][0]["source_url"] == "https://example.test/xhs"


def test_knowledge_search_enforces_owner_and_tenant_acl(tmp_path) -> None:
    (tmp_path / "private.md").write_text(
        """---
title: 私有流程
tenant: studio-a
allowed_owners: ["user:alice"]
---
内部交付暗号是蓝色文件夹。
""",
        encoding="utf-8",
    )
    build_knowledge_index(tmp_path)

    denied = KnowledgeSearchSkill(
        str(tmp_path),
        owner="user:bob",
        tenant="studio-a",
    ).run({"query": "交付暗号", "mode": "text"})
    wrong_tenant = KnowledgeSearchSkill(
        str(tmp_path),
        owner="user:alice",
        tenant="studio-b",
    ).run({"query": "交付暗号", "mode": "text"})
    allowed = KnowledgeSearchSkill(
        str(tmp_path),
        owner="user:alice",
        tenant="studio-a",
    ).run({"query": "交付暗号", "mode": "text"})

    assert denied.metadata["chunks"] == []
    assert wrong_tenant.metadata["chunks"] == []
    assert allowed.metadata["chunks"]


def test_knowledge_index_refreshes_when_source_changes(tmp_path) -> None:
    source = tmp_path / "guide.txt"
    source.write_text("第一版只讨论曝光。", encoding="utf-8")
    skill = KnowledgeSearchSkill(str(tmp_path), owner="anon:test")
    assert skill.run({"query": "曝光", "mode": "text"}).metadata["chunks"]

    source.write_text("第二版只讨论构图和裁切。", encoding="utf-8")
    refreshed = skill.run({"query": "构图裁切", "mode": "text"})
    assert refreshed.metadata["chunks"]
    assert "第二版" in refreshed.metadata["chunks"][0]["text"]


def test_chinese_tokenizer_produces_bigrams() -> None:
    tokens = tokenize("竖构图")
    assert "竖构" in tokens
    assert "构图" in tokens
