"""SigLIP vision encoder + linear head for human overall score (0–100)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "google/siglip-base-patch16-224"


@dataclass
class TrainConfig:
    model_id: str = DEFAULT_MODEL_ID
    training_mode: str = "regress"  # regress | rank
    seed: int = 42
    val_fraction: float = 0.2
    val_split: str = "random"  # random | session
    epochs: int = 25
    batch_size: int = 8
    lr: float = 1e-3
    weight_decay: float = 1e-4
    freeze_vision: bool = True
    num_workers: int = 0
    device: str = "auto"
    # rank-only
    min_score_diff: float = 8.0
    rank_same_session_only: bool = True
    pairs_per_epoch: int = 640


def resolve_device(request: str) -> str:
    import torch

    if request and request != "auto":
        return request
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_eval_rows(
    labels_path: Path,
    images_dir: Path,
    *,
    manifest_path: Path | None = None,
) -> list[dict[str, Any]]:
    sessions: dict[str, str] = {}
    if manifest_path and manifest_path.is_file():
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
        for it in man.get("items") or []:
            if isinstance(it, dict) and it.get("file"):
                sessions[str(it["file"])] = str(it.get("session") or "")

    rows: list[dict[str, Any]] = []
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        file = rec.get("file")
        overall = rec.get("overall")
        if not file or overall is None:
            continue
        path = images_dir / file
        if not path.is_file():
            logger.warning("skip missing image: %s", path)
            continue
        rows.append(
            {
                "file": file,
                "overall": float(overall),
                "path": str(path.resolve()),
                "session": sessions.get(file, ""),
            }
        )
    return rows


def open_eval_image(path: str | Path) -> Image.Image:
    from services.jpeg_exif_orientation import open_display_ready_image, resolve_capture_rotation_degrees

    p = str(path)
    return open_display_ready_image(p, resolve_capture_rotation_degrees(p))


class EvalImageDataset:
    """Lazy PIL load + processor batching handled in train loop."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        r = self.rows[idx]
        img = open_eval_image(r["path"]).convert("RGB")
        return {"file": r["file"], "overall": r["overall"], "image": img}


def train_val_split(rows: list[dict[str, Any]], val_fraction: float, seed: int) -> tuple[list, list]:
    rng = np.random.default_rng(seed)
    idx = np.arange(len(rows))
    rng.shuffle(idx)
    n_val = max(1, int(round(len(rows) * val_fraction)))
    val_idx = set(idx[:n_val].tolist())
    train = [rows[i] for i in range(len(rows)) if i not in val_idx]
    val = [rows[i] for i in range(len(rows)) if i in val_idx]
    return train, val


def train_val_split_by_session(rows: list[dict[str, Any]], val_fraction: float, seed: int) -> tuple[list, list]:
    """Hold out whole sessions so pair rank does not leak same-show cues."""
    by_sess: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_sess.setdefault(r.get("session") or "_unknown", []).append(r)
    sessions = sorted(by_sess.keys())
    rng = np.random.default_rng(seed)
    rng.shuffle(sessions)
    n_val = max(1, int(round(len(sessions) * val_fraction)))
    val_set = set(sessions[:n_val])
    train, val = [], []
    for s, items in by_sess.items():
        (val if s in val_set else train).extend(items)
    return train, val


def build_rank_pairs(
    rows: list[dict[str, Any]],
    *,
    min_score_diff: float,
    same_session_only: bool,
) -> list[tuple[int, int]]:
    """Ordered pairs (winner_idx, loser_idx) with human score gap >= min_score_diff."""
    pairs: list[tuple[int, int]] = []

    def _add_pairs(indices: list[int]) -> None:
        for wi in indices:
            for li in indices:
                if wi == li:
                    continue
                if rows[wi]["overall"] >= rows[li]["overall"] + min_score_diff:
                    pairs.append((wi, li))

    if same_session_only:
        by_sess: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            by_sess.setdefault(r.get("session") or "_unknown", []).append(i)
        for indices in by_sess.values():
            if len(indices) >= 2:
                _add_pairs(indices)
    else:
        _add_pairs(list(range(len(rows))))
    return pairs


def config_from_checkpoint(raw: dict[str, Any]) -> TrainConfig:
    from dataclasses import fields

    names = {f.name for f in fields(TrainConfig)}
    return TrainConfig(**{k: v for k, v in raw.items() if k in names})


def build_model(cfg: TrainConfig):
    import torch
    import torch.nn as nn
    from transformers import AutoModel

    backbone = AutoModel.from_pretrained(cfg.model_id)
    if not hasattr(backbone, "get_image_features"):
        raise TypeError(f"{cfg.model_id} does not expose get_image_features")
    hidden = int(backbone.config.vision_config.hidden_size)
    head = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, 1))

    class SiglipOverallRegressor(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.backbone = backbone
            self.head = head
            if cfg.freeze_vision:
                for p in self.backbone.parameters():
                    p.requires_grad = False

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            out = self.backbone.get_image_features(pixel_values=pixel_values)
            if isinstance(out, torch.Tensor):
                feats = out
            else:
                feats = out.pooler_output
            return self.head(feats).squeeze(-1)

    return SiglipOverallRegressor()


def collate_batch(processor, items: list[dict[str, Any]]):
    import torch

    files = [x["file"] for x in items]
    targets = torch.tensor([x["overall"] for x in items], dtype=torch.float32)
    pixels = processor(images=[x["image"] for x in items], return_tensors="pt")["pixel_values"]
    return files, pixels, targets


@dataclass
class EpochMetrics:
    loss: float
    spearman: float
    mae: float
    pair_acc: float | None = None


def run_epoch(model, processor, rows, device, *, train: bool, optimizer, batch_size: int) -> EpochMetrics:
    import torch
    from scripts.eval.metrics import mae, spearman

    model.train(train)
    losses: list[float] = []
    preds: list[float] = []
    human: list[float] = []
    ds = EvalImageDataset(rows)
    order = list(range(len(ds)))
    if train:
        rng = np.random.default_rng()
        rng.shuffle(order)

    for start in range(0, len(ds), batch_size):
        indices = order[start : min(start + batch_size, len(ds))]
        chunk = [ds[i] for i in indices]
        _files, pixels, targets = collate_batch(processor, chunk)
        pixels = pixels.to(device)
        targets = targets.to(device)
        with torch.set_grad_enabled(train):
            pred = model(pixels)
            loss = torch.nn.functional.smooth_l1_loss(pred, targets)
            if train:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
        losses.append(float(loss.detach().cpu()))
        preds.extend(pred.detach().cpu().tolist())
        human.extend(targets.detach().cpu().tolist())

    sp = spearman(human, preds)
    return EpochMetrics(loss=float(np.mean(losses)), spearman=sp, mae=mae(human, preds))


def _scores_for_rows(model, processor, rows, device, batch_size: int) -> tuple[list[float], list[float]]:
    import torch

    model.eval()
    ds = EvalImageDataset(rows)
    preds: list[float] = []
    human: list[float] = []
    with torch.no_grad():
        for start in range(0, len(ds), batch_size):
            chunk = [ds[i] for i in range(start, min(start + batch_size, len(ds)))]
            _files, pixels, targets = collate_batch(processor, chunk)
            pred = model(pixels.to(device)).cpu().tolist()
            if not isinstance(pred, list):
                pred = [pred]
            preds.extend(pred)
            human.extend(targets.cpu().tolist())
    return human, preds


def run_rank_epoch(
    model,
    processor,
    rows: list[dict[str, Any]],
    device,
    *,
    train: bool,
    optimizer,
    batch_size: int,
    cfg: TrainConfig,
    pair_indices: list[tuple[int, int]],
    rng: np.random.Generator,
) -> EpochMetrics:
    import torch
    from scripts.eval.metrics import mae, spearman

    if not pair_indices:
        human, preds = _scores_for_rows(model, processor, rows, device, batch_size)
        return EpochMetrics(
            loss=float("nan"),
            spearman=spearman(human, preds),
            mae=mae(human, preds),
            pair_acc=float("nan"),
        )

    model.train(train)
    ds = EvalImageDataset(rows)
    n_steps = max(1, cfg.pairs_per_epoch // max(1, batch_size))
    losses: list[float] = []
    correct = 0
    total_pairs = 0

    for _ in range(n_steps):
        pick = rng.integers(0, len(pair_indices), size=batch_size)
        win_imgs, lose_imgs = [], []
        for p in pick:
            wi, li = pair_indices[int(p)]
            win_imgs.append(ds[wi]["image"])
            lose_imgs.append(ds[li]["image"])
        w_pix = processor(images=win_imgs, return_tensors="pt")["pixel_values"].to(device)
        l_pix = processor(images=lose_imgs, return_tensors="pt")["pixel_values"].to(device)
        with torch.set_grad_enabled(train):
            s_w = model(w_pix)
            s_l = model(l_pix)
            loss = torch.nn.functional.softplus(-(s_w - s_l)).mean()
            if train:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
        losses.append(float(loss.detach().cpu()))
        correct += int((s_w > s_l).sum().item())
        total_pairs += batch_size

    human, preds = _scores_for_rows(model, processor, rows, device, batch_size)
    pair_acc = correct / total_pairs if total_pairs else float("nan")
    return EpochMetrics(
        loss=float(np.mean(losses)),
        spearman=spearman(human, preds),
        mae=mae(human, preds),
        pair_acc=pair_acc,
    )


def save_checkpoint(out_dir: Path, model, processor, cfg: TrainConfig, metrics: dict[str, Any]) -> None:
    import torch

    out_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out_dir / "regressor.pt")
    processor.save_pretrained(out_dir / "processor")
    (out_dir / "train_config.json").write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def load_regressor(checkpoint_dir: Path, device: str):
    import torch
    from transformers import AutoImageProcessor

    cfg_raw = json.loads((checkpoint_dir / "train_config.json").read_text(encoding="utf-8"))
    cfg = config_from_checkpoint(cfg_raw)
    processor = AutoImageProcessor.from_pretrained(checkpoint_dir / "processor")
    model = build_model(cfg)
    state = torch.load(checkpoint_dir / "regressor.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    return model, processor, cfg


def predict_rows(
    model,
    processor,
    rows: list[dict[str, Any]],
    device: str,
    batch_size: int,
    *,
    training_mode: str = "regress",
) -> list[dict[str, Any]]:
    import torch

    model.eval()
    ds = EvalImageDataset(rows)
    out: list[dict[str, Any]] = []
    with torch.no_grad():
        for start in range(0, len(ds), batch_size):
            chunk = [ds[i] for i in range(start, min(start + batch_size, len(ds)))]
            files, pixels, _ = collate_batch(processor, chunk)
            pred = model(pixels.to(device)).cpu().tolist()
            if not isinstance(pred, list):
                pred = [pred]
            for f, s in zip(files, pred):
                score = float(s)
                if training_mode == "regress":
                    score = max(0.0, min(100.0, score))
                out.append({"file": f, "overall_score": round(score, 2)})
    return out
