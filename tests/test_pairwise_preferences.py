from services.pairwise_preferences import aggregate_win_loss_keys, pairwise_edge_records
from utils.pairwise_preferences import (
    append_pairwise_preferences,
    clear_pairwise_preferences,
    read_pairwise_preferences,
)


def test_append_and_read_pairwise(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    out = append_pairwise_preferences(
        previews,
        [
            {
                "winner_key": "/p/a.jpg",
                "loser_key": "/p/b.jpg",
                "group_id": "burst-1",
                "reason_tags": ["moment", "composition"],
                "source": "burst",
            }
        ],
    )
    assert out["ok"] is True
    data = read_pairwise_preferences(previews)
    assert data is not None
    assert data["version"] == 1
    assert len(data["entries"]) == 1
    e = data["entries"][0]
    assert e["winner_key"] == "/p/a.jpg"
    assert e["loser_key"] == "/p/b.jpg"
    assert e["group_id"] == "burst-1"
    assert e["reason_tags"] == ["moment", "composition"]


def test_replace_same_pair_in_group(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    append_pairwise_preferences(
        previews,
        [{"winner_key": "a", "loser_key": "b", "group_id": "g1", "reason_tags": ["light"]}],
    )
    append_pairwise_preferences(
        previews,
        [{"winner_key": "a", "loser_key": "b", "group_id": "g1", "reason_tags": ["moment"]}],
        replace_same_pair_in_group=True,
    )
    data = read_pairwise_preferences(previews)
    assert len(data["entries"]) == 1
    assert data["entries"][0]["reason_tags"] == ["moment"]


def test_edge_records_for_ranking(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    append_pairwise_preferences(
        previews,
        [
            {"winner_key": "w.jpg", "loser_key": "l.jpg", "reason_tags": ["atmosphere"]},
        ],
    )
    edges = pairwise_edge_records(previews)
    assert len(edges) == 1
    assert edges[0]["winner_key"] == "w.jpg"
    assert edges[0]["weight"] > 1.0
    wins, losses = aggregate_win_loss_keys(previews)
    assert wins["w.jpg"] == 1
    assert losses["l.jpg"] == 1
    clear_pairwise_preferences(previews)
    assert read_pairwise_preferences(previews) is None
