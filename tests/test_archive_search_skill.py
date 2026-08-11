from __future__ import annotations

import json

import numpy as np

from services.agent.conversation import ConversationalAgent
from services.agent.skills.archive_search import ArchiveSearchSkill
from services.agent.skills.base import SkillResult
from services.agent.skills.gallery import gallery_registry
from services.archive_photo_index import archive_index_dir, build_archive_photo_index
from scripts.eval.eval_archive_search_retrieval import evaluate


def _write_session(root, name: str, rows: list[dict]):
    previews = root / name / "Previews"
    previews.mkdir(parents=True)
    (previews / "analysis_results.json").write_text(
        json.dumps(rows, ensure_ascii=False),
        encoding="utf-8",
    )
    return previews


def _row(file: str, *, tags: list[str], reason: str, score: float = 80.0) -> dict:
    return {
        "file": file,
        "overall_score": score,
        "scores": {"overall": score, "technical": 8.0, "composition": 8.0},
        "category": "AI_Keep_60-90",
        "tags": tags,
        "reason": reason,
    }


def test_archive_search_crosses_sessions_and_uses_unique_ids(tmp_path) -> None:
    first = _write_session(
        tmp_path,
        "20260101_alpha",
        [_row("DSC0001.jpg", tags=["鼓手"], reason="红色灯光下击鼓")],
    )
    _write_session(
        tmp_path,
        "20260202_beta",
        [_row("DSC0001.jpg", tags=["鼓手", "互动"], reason="蓝色舞台上的鼓手")],
    )

    result = ArchiveSearchSkill(str(first)).run(
        {"query": "鼓手", "mode": "text", "limit": 10}
    )

    assert result.ok
    files = result.metadata["files"]
    assert set(files) == {
        "20260202_beta__DSC0001.jpg",
        "20260101_alpha__DSC0001.jpg",
    }
    assert len(set(files)) == 2
    assert result.metadata["ui_action"] == "archive_search"
    assert result.metadata["retrieval"] == "archive_text"


def test_archive_search_session_filter_and_current_exclusion(tmp_path) -> None:
    current = _write_session(
        tmp_path,
        "20260101_alpha",
        [_row("a.jpg", tags=["吉他手"], reason="吉他独奏")],
    )
    _write_session(
        tmp_path,
        "20260202_beta",
        [_row("b.jpg", tags=["吉他手"], reason="吉他互动")],
    )
    skill = ArchiveSearchSkill(str(current))

    hinted = skill.run(
        {"query": "吉他手", "mode": "text", "session_hint": "beta"}
    )
    assert hinted.metadata["files"] == ["20260202_beta__b.jpg"]

    excluded = skill.run(
        {"query": "吉他手", "mode": "text", "exclude_current_session": True}
    )
    assert excluded.metadata["files"] == ["20260202_beta__b.jpg"]


def test_clip_scores_use_prebuilt_vectors_without_encoding_images(tmp_path, monkeypatch) -> None:
    index_dir = archive_index_dir(tmp_path)
    vectors = index_dir / "vectors"
    vectors.mkdir(parents=True)
    np.save(vectors / "a.npy", np.array([1.0, 0.0], dtype=np.float32))
    np.save(vectors / "b.npy", np.array([0.0, 1.0], dtype=np.float32))
    monkeypatch.setattr(
        "services.embedding_service.EmbeddingService.embed_text",
        classmethod(lambda cls, query: np.array([1.0, 0.0], dtype=np.float32)),
    )

    scores = ArchiveSearchSkill._clip_scores(
        "吉他手",
        [
            {"archive_id": "a.jpg", "vector_path": "vectors/a.npy"},
            {"archive_id": "b.jpg", "vector_path": "vectors/b.npy"},
        ],
        index_dir=index_dir,
    )

    assert scores["a.jpg"] == 1.0
    assert scores["b.jpg"] == 0.0


def test_archive_index_and_registry_contract(tmp_path) -> None:
    current = _write_session(
        tmp_path,
        "20260101_alpha",
        [_row("a.jpg", tags=["主唱"], reason="主唱特写")],
    )
    meta = build_archive_photo_index(tmp_path)

    assert meta["session_count"] == 1
    assert meta["row_count"] == 1
    assert "archive_search" in gallery_registry(str(current)).names()


def test_archive_hits_do_not_replace_current_selection_memory() -> None:
    agent = ConversationalAgent(
        lambda _messages: "ok",
        working_memory={"last_files": ["selected.jpg"], "last_query": "本场"},
    )
    agent._update_working_memory(
        "archive_search",
        {"query": "历史鼓手"},
        SkillResult(
            ok=True,
            metadata={"files": ["20260101__drummer.jpg"]},
        ),
    )

    assert agent.working_memory["last_files"] == ["selected.jpg"]
    assert agent.working_memory["last_archive_hits"] == ["20260101__drummer.jpg"]
    assert agent.working_memory["last_archive_query"] == "历史鼓手"


def test_archive_retrieval_eval_reports_recall_and_mrr(tmp_path) -> None:
    _write_session(
        tmp_path,
        "20260101_alpha",
        [_row("drum.jpg", tags=["鼓手"], reason="鼓手特写")],
    )
    report = evaluate(
        archive_root=tmp_path,
        cases=[
            {
                "id": "drummer",
                "query": "鼓手",
                "args": {"mode": "text"},
                "expected_files": ["20260101_alpha__drum.jpg"],
                "k": 5,
            }
        ],
    )

    assert report["passed"] == 1
    assert report["metrics"]["recall_at_k"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
