import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.eval.build_hard_negative_packs import build_hard_negative_manifest
from scripts.eval.build_pack_dev_set import build_pack_manifest
from scripts.eval.eval_pack_reranker import _ndcg_at_five, _risk_score, _score_pack
from scripts.eval.materialize_pack_scoring_set import materialize_missing
from scripts.eval.merge_pack_predictions import merge_pack_predictions
from scripts.eval.pack_review_server import Handler, ReviewStore
from scripts.eval.run_pack_comparative_vlm import build_contact_sheet, validate_result


def test_build_pack_manifest_groups_sessions_and_holds_out_whole_packs(
    tmp_path: Path,
) -> None:
    items = []
    predictions = []
    for session in ("session_a", "session_b"):
        for index in range(8):
            file_id = f"{session}_{index}.jpg"
            items.append(
                {
                    "file": file_id,
                    "session": session,
                    "source_path": f"/images/{file_id}",
                }
            )
            predictions.append(
                {"file": file_id, "overall_score": 70 + index}
            )
    manifest = tmp_path / "manifest.json"
    prediction_path = tmp_path / "predictions.json"
    output = tmp_path / "packs.json"
    manifest.write_text(json.dumps({"items": items}), encoding="utf-8")
    prediction_path.write_text(json.dumps(predictions), encoding="utf-8")

    result = build_pack_manifest(
        dataset_manifest_path=manifest,
        predictions_path=prediction_path,
        output_path=output,
        min_size=8,
        max_size=15,
        holdout_fraction=0.5,
    )

    assert result["pack_count"] == 2
    assert result["holdout_pack_count"] == 1
    assert all(len(pack["files"]) == 8 for pack in result["packs"])
    assert result["packs"][0]["files"][0].endswith("_7.jpg")


def test_pack_review_requires_ordered_top_five_disjoint_from_flags() -> None:
    files = [f"{index}.jpg" for index in range(8)]
    handler = Handler.__new__(Handler)
    handler.packs_by_id = {
        "pack": {
            "id": "pack",
            "session": "session",
            "split": "development",
            "files": files,
        }
    }
    handler.reviewer = "tester"
    handler.min_excluded = 0

    record = handler._sanitize_review(
        {
            "pack_id": "pack",
            "selected_ids": files[:5],
            "excluded_ids": [files[5]],
            "duplicate_ids": [files[6]],
        }
    )
    assert record["selected_ids"] == files[:5]

    with pytest.raises(ValueError, match="cannot also"):
        handler._sanitize_review(
            {
                "pack_id": "pack",
                "selected_ids": files[:5],
                "excluded_ids": [files[0]],
            }
        )


def test_review_store_upserts_by_pack_id(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "reviews.jsonl")
    store.upsert({"pack_id": "pack", "selected_ids": ["a"]})
    store.upsert({"pack_id": "pack", "selected_ids": ["b"]})
    assert store.read_all() == [{"pack_id": "pack", "selected_ids": ["b"]}]


def test_pack_metrics_and_risk_penalty_preserve_human_order_signal() -> None:
    human = ["a", "b", "c", "d", "e"]
    assert _ndcg_at_five(human, human) == pytest.approx(1.0)
    assert _ndcg_at_five(list(reversed(human)), human) < 1.0

    row = {
        "overall_score": 90,
        "dimensions": {
            "composition_framing": 6,
            "deliverable_subject": 7,
            "focus_sharpness": 7,
        },
    }
    assert _risk_score(row, (4, 2, 2)) == pytest.approx(79.0)
    scored = _score_pack(
        human,
        human,
        excluded_ids={"a"},
        duplicate_ids={"b"},
    )
    assert scored["zero_blunder"] is False
    assert scored["excluded_count"] == 1
    assert scored["duplicate_count"] == 1


def test_contact_sheet_mapping_and_comparative_result_validation(
    tmp_path: Path,
) -> None:
    source_paths = {}
    for index in range(8):
        file_id = f"{index}.jpg"
        path = tmp_path / file_id
        Image.new("RGB", (40, 30), (index * 20, 0, 0)).save(path)
        source_paths[file_id] = str(path)
    pack = {
        "id": "pack",
        "files": list(source_paths),
        "source_paths": source_paths,
    }
    label_map = build_contact_sheet(
        pack,
        tmp_path / "sheet.jpg",
        tile_width=80,
        tile_height=60,
    )
    ranked_labels = list(label_map)[:5]
    result = validate_result(
        {
            "ranked_top5": ranked_labels,
            "must_exclude": [list(label_map)[5]],
            "weaker_duplicates": ["not-a-label"],
        },
        label_map,
    )
    assert result["ranked_top5"] == [label_map[label] for label in ranked_labels]
    assert len(result["must_exclude"]) == 1
    assert result["weaker_duplicates"] == []

    with pytest.raises(ValueError, match="five unique"):
        validate_result({"ranked_top5": ["A"] * 5}, label_map)


def test_hard_pack_builder_mixes_high_score_defects_and_acceptable_rows(
    tmp_path: Path,
) -> None:
    items = []
    predictions = []
    defects = {}
    acceptable = []
    for index in range(10):
        file_id = f"session__DSC{index:05d}.jpg"
        source = tmp_path / file_id
        Image.new("RGB", (20, 20)).save(source)
        items.append(
            {
                "file": file_id,
                "session": "session",
                "source_path": str(source),
            }
        )
        predictions.append({"file": file_id, "overall_score": 90 - index})
        if index < 4:
            defects[file_id] = {"reasons": ["composition_failure"]}
        else:
            acceptable.append(file_id)

    paths = {
        "manifest": tmp_path / "manifest.json",
        "defects": tmp_path / "defects.json",
        "acceptable": tmp_path / "acceptable.json",
        "predictions": tmp_path / "predictions.json",
        "output": tmp_path / "hard_packs.json",
    }
    paths["manifest"].write_text(json.dumps({"items": items}), encoding="utf-8")
    paths["defects"].write_text(json.dumps(defects), encoding="utf-8")
    paths["acceptable"].write_text(json.dumps(acceptable), encoding="utf-8")
    paths["predictions"].write_text(json.dumps(predictions), encoding="utf-8")

    result = build_hard_negative_manifest(
        frozen_manifest_path=paths["manifest"],
        defects_path=paths["defects"],
        acceptable_path=paths["acceptable"],
        predictions_path=paths["predictions"],
        output_path=paths["output"],
        base_size=10,
        duplicate_candidates=0,
    )

    assert result["pack_count"] == 1
    assert result["known_defect_count"] == 4
    assert len(result["packs"][0]["files"]) == 10


def test_materialize_pack_scoring_set_skips_cached_predictions(tmp_path: Path) -> None:
    source_a = tmp_path / "source_a.jpg"
    source_b = tmp_path / "source_b.jpg"
    source_a.write_bytes(b"a")
    source_b.write_bytes(b"b")
    packs = tmp_path / "packs.json"
    predictions = tmp_path / "predictions.json"
    output = tmp_path / "input"
    packs.write_text(
        json.dumps(
            {
                "packs": [
                    {
                        "id": "pack",
                        "files": ["a.jpg", "b.jpg"],
                        "source_paths": {
                            "a.jpg": str(source_a),
                            "b.jpg": str(source_b),
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    predictions.write_text(json.dumps([{"file": "a.jpg"}]), encoding="utf-8")

    result = materialize_missing(
        pack_manifest_path=packs,
        existing_predictions_path=predictions,
        output_dir=output,
    )

    assert result["image_count"] == 1
    assert (output / "b.jpg").read_bytes() == b"b"
    assert not (output / "a.jpg").exists()

    second_predictions = tmp_path / "second_predictions.json"
    merged = tmp_path / "merged.json"
    second_predictions.write_text(json.dumps([{"file": "b.jpg"}]), encoding="utf-8")
    rows = merge_pack_predictions(
        pack_manifest_path=packs,
        prediction_paths=[predictions, second_predictions],
        output_path=merged,
    )
    assert {row["file"] for row in rows} == {"a.jpg", "b.jpg"}
