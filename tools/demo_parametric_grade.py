"""Minimal vertical-slice demo: VLM-style numeric edit -> baked image.

Usage:
    python -m tools.demo_parametric_grade <image_path> [--out demo_out.jpg] \
        [--adjust '{"exposure":0.7,"shadows":20,"highlights":-30}']

If no image is passed, a synthetic gradient is used so the pipeline is runnable
without test assets. Writes a side-by-side before/after JPEG.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from op_kernel import apply_parametric_grade
from services.edit_adjustments import edit_adjustments_from_dict, parse_edit_adjustments_response

# A "what the VLM would emit" default — mimics a dim livehouse frame correction.
_DEFAULT_VLM_JSON = (
    '{"adjustments":{"exposure":0.7,"contrast":12,"highlights":-35,'
    '"shadows":25,"whites":-5,"blacks":-8,"temp":-6,"tint":3,'
    '"vibrance":18,"saturation":-4,"clarity":10}}'
)


def _synthetic_image(h: int = 720, w: int = 1080) -> np.ndarray:
    yy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    r = (0.35 + 0.5 * xx) * (0.4 + 0.6 * yy)
    g = (0.30 + 0.4 * yy)
    b = (0.55 + 0.4 * (1.0 - xx)) * (0.3 + 0.5 * yy)
    img = np.stack([r, g, b], axis=2)
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", default=None)
    ap.add_argument("--out", default="demo_parametric_grade.jpg")
    ap.add_argument("--adjust", default=None, help="raw VLM JSON or bare adjustments object")
    args = ap.parse_args()

    if args.image:
        before = np.array(Image.open(args.image).convert("RGB"), dtype=np.uint8)
    else:
        before = _synthetic_image()
        print("No image given — using synthetic gradient.")

    if args.adjust:
        try:
            payload = json.loads(args.adjust)
            adj = edit_adjustments_from_dict(payload.get("adjustments", payload))
        except json.JSONDecodeError:
            adj = parse_edit_adjustments_response(args.adjust)
    else:
        adj = parse_edit_adjustments_response(_DEFAULT_VLM_JSON)

    print(f"Adjustments (clamped): {adj.as_dict()}")
    print(f"cache_token: {adj.cache_token()!r}  is_active={adj.is_active()}")

    after = apply_parametric_grade(before, adj)

    gap = np.full((before.shape[0], 12, 3), 255, dtype=np.uint8)
    combo = np.concatenate([before, gap, after], axis=1)
    out_path = Path(args.out).resolve()
    Image.fromarray(combo).save(out_path, quality=92)
    print(f"Wrote before|after to: {out_path}")


if __name__ == "__main__":
    main()
