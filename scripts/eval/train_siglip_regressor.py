#!/usr/bin/env python3
"""Train / evaluate SigLIP scorers on eval human overall (regression or pairwise rank)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.labels import load_labels, load_predictions, join_labels_predictions
from scripts.eval.metrics import mae, spearman
from scripts.eval.siglip_scorer import (
    TrainConfig,
    build_model,
    build_rank_pairs,
    load_eval_rows,
    load_regressor,
    predict_rows,
    resolve_device,
    run_epoch,
    run_rank_epoch,
    save_checkpoint,
    train_val_split,
    train_val_split_by_session,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _split_rows(rows, cfg: TrainConfig) -> tuple[list, list]:
    if cfg.val_split == "session":
        return train_val_split_by_session(rows, cfg.val_fraction, cfg.seed)
    return train_val_split(rows, cfg.val_fraction, cfg.seed)


def _cmd_train(args: argparse.Namespace) -> int:
    import numpy as np
    import torch
    from transformers import AutoImageProcessor

    labels_path = Path(args.labels)
    images_dir = Path(args.images)
    manifest_path = Path(args.manifest) if args.manifest else None
    out_dir = Path(args.out)

    cfg = TrainConfig(
        model_id=args.model_id,
        training_mode=args.mode,
        seed=args.seed,
        val_fraction=args.val_fraction,
        val_split=args.val_split,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        freeze_vision=not args.finetune_vision,
        min_score_diff=args.min_score_diff,
        rank_same_session_only=not args.rank_cross_session,
        pairs_per_epoch=args.pairs_per_epoch,
    )
    device = resolve_device(args.device)
    logger.info(
        "device=%s mode=%s model=%s freeze_vision=%s val_split=%s",
        device,
        cfg.training_mode,
        cfg.model_id,
        cfg.freeze_vision,
        cfg.val_split,
    )

    rows = load_eval_rows(labels_path, images_dir, manifest_path=manifest_path)
    if len(rows) < 20:
        logger.error("need at least 20 labeled images, got %d", len(rows))
        return 1
    train_rows, val_rows = _split_rows(rows, cfg)
    logger.info("train=%d val=%d (rows)", len(train_rows), len(val_rows))

    train_pairs = (
        build_rank_pairs(
            train_rows,
            min_score_diff=cfg.min_score_diff,
            same_session_only=cfg.rank_same_session_only,
        )
        if cfg.training_mode == "rank"
        else []
    )
    val_pairs = (
        build_rank_pairs(
            val_rows,
            min_score_diff=cfg.min_score_diff,
            same_session_only=cfg.rank_same_session_only,
        )
        if cfg.training_mode == "rank"
        else []
    )
    if cfg.training_mode == "rank":
        logger.info("rank pairs train=%d val=%d (min_diff=%.1f)", len(train_pairs), len(val_pairs), cfg.min_score_diff)

    processor = AutoImageProcessor.from_pretrained(cfg.model_id)
    model = build_model(cfg).to(device)
    optim = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=cfg.lr,
        weight_decay=cfg.weight_decay,
    )
    rng = np.random.default_rng(cfg.seed)

    best_val_sp = float("-inf")
    history: list[dict] = []
    for epoch in range(1, cfg.epochs + 1):
        if cfg.training_mode == "rank":
            tr = run_rank_epoch(
                model,
                processor,
                train_rows,
                device,
                train=True,
                optimizer=optim,
                batch_size=cfg.batch_size,
                cfg=cfg,
                pair_indices=train_pairs,
                rng=rng,
            )
            va = run_rank_epoch(
                model,
                processor,
                val_rows,
                device,
                train=False,
                optimizer=optim,
                batch_size=cfg.batch_size,
                cfg=cfg,
                pair_indices=val_pairs,
                rng=rng,
            )
        else:
            tr = run_epoch(model, processor, train_rows, device, train=True, optimizer=optim, batch_size=cfg.batch_size)
            va = run_epoch(model, processor, val_rows, device, train=False, optimizer=optim, batch_size=cfg.batch_size)

        row = {"epoch": epoch, "train": tr.__dict__, "val": va.__dict__}
        history.append(row)
        extra = f" pair_acc={va.pair_acc:.3f}" if va.pair_acc is not None and va.pair_acc == va.pair_acc else ""
        logger.info(
            "epoch %d/%d  train loss=%.3f sp=%.3f  val loss=%.3f sp=%.3f mae=%.2f%s",
            epoch,
            cfg.epochs,
            tr.loss,
            tr.spearman,
            va.loss,
            va.spearman,
            va.mae,
            extra,
        )
        if va.spearman > best_val_sp:
            best_val_sp = va.spearman
            save_checkpoint(
                out_dir,
                model,
                processor,
                cfg,
                {
                    "best_val_spearman": best_val_sp,
                    "epoch": epoch,
                    "history": history,
                    "train_pairs": len(train_pairs),
                    "val_pairs": len(val_pairs),
                },
            )

    logger.info("best val Spearman=%.3f  checkpoint=%s", best_val_sp, out_dir)
    return 0


def _predictions_json_for_eval(records: list[dict]) -> list[dict]:
    return [{"file": r["file"], "overall_score": r["overall_score"], "score": r["overall_score"]} for r in records]


def _cmd_eval(args: argparse.Namespace) -> int:
    labels_path = Path(args.labels)
    images_dir = Path(args.images)
    ckpt = Path(args.checkpoint)

    device = resolve_device(args.device)
    model, processor, cfg = load_regressor(ckpt, device)
    rows = load_eval_rows(labels_path, images_dir, manifest_path=Path(args.manifest) if args.manifest else None)
    preds = predict_rows(
        model,
        processor,
        rows,
        device,
        batch_size=args.batch_size,
        training_mode=cfg.training_mode,
    )
    pred_path = Path(args.json) if args.json else ckpt / "predictions_eval.json"
    pred_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _predictions_json_for_eval(preds)
    pred_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("wrote %d predictions to %s", len(payload), pred_path)

    labels = load_labels(str(labels_path))
    joined = join_labels_predictions(labels, load_predictions(str(pred_path)))
    human, ai = [], []
    for lab, pred in joined.pairs:
        if lab.overall is None or pred.overall is None:
            continue
        human.append(lab.overall)
        ai.append(pred.overall)
    mid_h, mid_a = [], []
    for lab, pred in joined.pairs:
        if lab.overall is None or pred.overall is None or lab.overall < 40:
            continue
        mid_h.append(lab.overall)
        mid_a.append(pred.overall)

    title = "SigLIP rank" if cfg.training_mode == "rank" else "SigLIP regressor"
    print("=" * 56)
    print(f"{title} vs human labels")
    print(f"n={len(human)}  Spearman={spearman(human, ai):.3f}  MAE={mae(human, ai):.2f}")
    if mid_h:
        print(f"mid (human>=40, n={len(mid_h)})  Spearman={spearman(mid_h, mid_a):.3f}")
    print("=" * 56)
    if args.compare_v4:
        v4_path = Path(args.compare_v4)
        if v4_path.is_file():
            j2 = join_labels_predictions(labels, load_predictions(str(v4_path)))
            h2, a2 = [], []
            for lab, pred in j2.pairs:
                if lab.overall is None or pred.overall is None:
                    continue
                h2.append(lab.overall)
                a2.append(pred.overall)
            print(f"VLM baseline ({v4_path.name}): Spearman={spearman(h2, a2):.3f}  MAE={mae(h2, a2):.2f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("train", help="train regressor or rank head")
    pt.add_argument("--labels", default="data/eval/labels.jsonl")
    pt.add_argument("--images", default="data/eval/images")
    pt.add_argument("--manifest", default="data/eval/manifest.json")
    pt.add_argument("--out", default="models/eval/siglip_overall_v1")
    pt.add_argument("--mode", choices=("regress", "rank"), default="regress")
    pt.add_argument("--model-id", default=TrainConfig.model_id)
    pt.add_argument("--seed", type=int, default=42)
    pt.add_argument("--val-fraction", type=float, default=0.2)
    pt.add_argument("--val-split", choices=("random", "session"), default="session")
    pt.add_argument("--epochs", type=int, default=25)
    pt.add_argument("--batch-size", type=int, default=8)
    pt.add_argument("--lr", type=float, default=1e-3)
    pt.add_argument("--device", default="auto")
    pt.add_argument("--finetune-vision", action="store_true")
    pt.add_argument("--min-score-diff", type=float, default=8.0)
    pt.add_argument("--rank-cross-session", action="store_true", help="allow pairs from different sessions")
    pt.add_argument("--pairs-per-epoch", type=int, default=640)
    pt.set_defaults(func=_cmd_train)

    pe = sub.add_parser("eval", help="run checkpoint on all labeled images")
    pe.add_argument("--checkpoint", required=True)
    pe.add_argument("--labels", default="data/eval/labels.jsonl")
    pe.add_argument("--images", default="data/eval/images")
    pe.add_argument("--manifest", default="data/eval/manifest.json")
    pe.add_argument("--json", default="")
    pe.add_argument("--batch-size", type=int, default=16)
    pe.add_argument("--device", default="auto")
    pe.add_argument(
        "--compare-v4",
        default="reports/eval/baseline_v4_stage1_two_merged_predictions.json",
    )
    pe.set_defaults(func=_cmd_eval)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
