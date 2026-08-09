#!/usr/bin/env python3
"""AI-assisted relabel pass for the Stage3 golden set via Qwen-VL on DashScope.

Why this exists: the committed 250 labels in ``data/eval/labels.jsonl`` put
``overall`` on a 15-value grid (67% sit on exactly 70/75/80) and leave all eight
dimensions null, which caps rank metrics no matter how good Stage3 gets. This
script produces a fine-grained 8-dimension suggestion per image so a human
reviews and corrects instead of scoring 250 photos from scratch.

Read before trusting the output:

* Suggestions are **not** ground truth. Stage3 scores with Qwen2-VL locally, so
  labeling with a Qwen-VL model shares family bias -- accepting suggestions
  unedited would inflate Stage3 Spearman against itself. Review every row in
  ``scripts/review_server.py``, and keep an unanchored control via
  ``--blind-fraction`` so the anchoring effect stays measurable.
* ``overall`` is derived from the eight dimensions (``DIM_WEIGHTS``) rather than
  asked from the model, so the score distribution is continuous by construction
  and every value is explainable from its parts.

Usage::

    export DASHSCOPE_API_KEY=sk-...
    python scripts/eval/relabel_qwen.py models             # what this account can call
    python scripts/eval/relabel_qwen.py score --limit 5    # smoke 5 images first
    python scripts/eval/relabel_qwen.py score              # full set, resumable
    python scripts/eval/relabel_qwen.py report             # AI vs existing human labels
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval.labels import DIM_KEYS, load_labels, normalize_name
from scripts.eval.protocol import stamp_protocol
from utils.stage3_dimensions import STAGE3_DIM_LABELS, STAGE3_DIM_PROMPT_LINES

SCHEMA_VERSION = "qwen_suggestion.v1"

DEFAULT_MODEL = "qwen3-vl-plus"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# Known DashScope VL slugs for scoring, most capable first. Availability is
# account-scoped -- run the `models` subcommand to see what a key can call.
# OCR variants are deliberately absent: they read text, they do not judge frames.
KNOWN_VL_MODELS = (
    "qwen3-vl-plus",
    "qwen3-vl-flash",
    "qwen-vl-max",
    "qwen-vl-plus",
)

# Delivery-weighted blend for `overall`. Sums to 1.0; override with --weights.
# Rationale: what a client receives is driven by whether the subject is usable and
# the moment landed; pure sensor hygiene (noise/exposure) matters least at a
# livehouse where high ISO and hard stage contrast are the normal operating point.
DIM_WEIGHTS: dict[str, float] = {
    "deliverable_subject": 0.20,
    "moment_peak": 0.18,
    "atmosphere_impact": 0.15,
    "focus_sharpness": 0.14,
    "composition_framing": 0.12,
    "light_color_character": 0.11,
    "exposure_control": 0.06,
    "noise_cleanliness": 0.04,
}

# Share of the pool suggested as keepers, matching the 83/250 human baseline.
DEFAULT_KEEP_TOP_PCT = 0.33

# Scoring the eight dimensions in ONE call collapses them: the first pass produced a
# mean off-diagonal Spearman of 0.642 between dimensions (focus<->noise 0.86,
# focus<->deliverable 0.84), i.e. the model formed a single "is this good" impression
# and spread it across eight slots. Three rounds of prompt wording did not move it.
# Splitting the call so sensor facts are judged without the frame's mood in context --
# and vice versa -- removes the shared anchor that wording alone could not.
DIM_GROUPS: dict[str, tuple[str, ...]] = {
    "technical": ("focus_sharpness", "exposure_control", "noise_cleanliness"),
    "perceptual": (
        "composition_framing",
        "light_color_character",
        "moment_peak",
        "atmosphere_impact",
        "deliverable_subject",
    ),
}

# A fatally soft or unusable subject cannot be rescued by mood, so cap the blend.
GATE_DIMS = ("focus_sharpness", "deliverable_subject")
GATE_THRESHOLD = 2.0
GATE_CAP = 35.0

# Transient HTTP failures worth another attempt; anything else is a config error.
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

_SCALE_ANCHORS = """0-2   fatal: unusable, no recovery path
3-4   weak: visible defect a client would reject
5-6   usable: fine in a bulk gallery, not a highlight
7-8   strong: would ship as a selected frame
9-10  exceptional: rare, portfolio-grade"""

# Observed failure on the first pass: light_color_character came back 8.4-9.4 on
# every frame (sd 0.32) because the model judged stage lighting against ordinary
# photography. Two near-constant dimensions carrying 0.26 weight added a flat
# ~22.6 points to everything, which is how an unusable frame scored 57.6.
_REFERENCE_CLASS = """Reference class -- this is the most common scoring error, read it twice:

Score each frame against OTHER LIVEHOUSE FRAMES FROM A SHOOT LIKE THIS ONE, not
against photography in general. Saturated coloured stage light, haze and hard
backlight are the BASELINE here; they are in nearly every frame. They are the
venue, not an achievement.

* light_color_character: 5 = an ordinary stage wash, where most frames belong.
  Reserve 8+ for light that is genuinely shaped -- real separation, direction, or
  a colour relationship a photographer would stop and point at. If you are about
  to give 9 to a frame that merely has strong colour, give 5.
* atmosphere_impact: 5 = the room simply reads as a gig. Reserve 8+ for a frame
  that actually transmits the energy of that moment.
* composition_framing: 5 = a competent grab shot, where most frames belong. Drop
  to 3-4 for a distracting crop, a limb cut at an awkward joint, or a foreground
  element that takes over the frame. Reserve 8+ for deliberate structure."""

# Written as a two-sided ladder on purpose. An earlier one-directional version
# ("silhouettes are fine, only fail when nothing reads") lifted this dimension's
# floor from 1.9 to 4.9 and cut its sd from 2.11 to 0.92, killing the most
# discriminative signal in the set. Exempt the silhouette, keep the bottom.
_SUBJECT_LADDER = """deliverable_subject anchors -- how usable the subject is in a delivered frame:

  9-10  subject unmistakable, expression or gesture reads fully, nothing competing
  7-8   subject reads clearly. A pure backlit silhouette with a strong body line
        belongs HERE, even with zero facial detail -- that is a signature look in
        this photographer's delivered work, not a defect
  5-6   subject identifiable but compromised: soft, partly occluded, small in the
        frame, or caught between beats with nothing happening
  3-4   you can tell a person is there but not what they are doing -- blur through
        the body line, or a foreground head or hand across the key area
  1-2   no usable subject at all

A dark or invisible face on its own puts a frame at 7-8, never below. But the
bottom of this ladder is real and a normal shoot reaches it: 5 is not a floor."""

_SYSTEM_PROMPT = f"""You are a senior live-music (livehouse) concert photography editor reviewing
frames from a real shoot for delivery to a client.

{{task_line}}

{{dim_block}}

Scale anchors:
{_SCALE_ANCHORS}
{{guidance_block}}
Livehouse context -- judge against what is achievable in this venue, not against
studio conditions:
* High ISO grain and deep shadows are normal. Penalize them only when they
  destroy information, not for existing.
* Motion blur can be deliberate and expressive. Penalize it when the subject
  becomes unreadable, not merely because it is blurred.
* A mic, hand or instrument crossing the face is acceptable when the emotion
  still reads.

Scoring discipline -- this matters as much as the scores themselves:
* Every scale runs down to 0, and a real shoot reaches the bottom: missed focus,
  the subject buried, nothing happening. When this frame is one of those, score it
  in the 1-4 band and let the total fall with it. Refusing to go below 5 is the
  single worst failure here, because it makes the frames worth discarding
  indistinguishable from the frames worth keeping.
* Being generous is not being fair. A 7 you hand out for free costs a genuinely
  strong frame the distance it earned.
* Use one decimal and vary it. Do not emit only .0 and .5 values.
* {{spread_line}}
* Judge only what is visible. Do not speculate about intent you cannot see.
* Do not decide whether the frame gets delivered. That is a comparison against
  the rest of the shoot, which you cannot see. Score this frame on its own.

Reply with ONE JSON object and nothing else -- no prose, no markdown fence:

{{schema_block}}"""

_USER_PROMPT = (
    "Score this livehouse frame. Return only the JSON object described in the "
    "system message, with one decimal on every dimension."
)


_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 8: "eight"}


def _dim_block(dims: Sequence[str]) -> str:
    return "\n".join(
        f"- {k} ({STAGE3_DIM_LABELS.get(k, k)}): {STAGE3_DIM_PROMPT_LINES.get(k, '')}"
        for k in dims
    )


def _schema_block(dims: Sequence[str]) -> str:
    lines = ["{"]
    for k in dims:
        lines.append(f'  "{k}": <number 0-10, one decimal>,')
    lines.extend(
        [
            '  "strongest_aspect": "<short phrase, zh>",',
            '  "weakest_aspect": "<short phrase, zh>",',
            '  "reason": "<one sentence, zh, why this frame scores where it does>",',
            '  "tags": ["<visible subject/scene tag>", "..."],',
            '  "confidence": <number 0-1, how sure you are about this frame>',
            "}",
        ]
    )
    return "\n".join(lines)


def build_system_prompt(dims: Sequence[str] | None = None) -> str:
    """Assemble the system prompt for ``dims`` (default: all eight, one call).

    Guidance blocks are included only when they bear on a dimension being asked
    for. That is not just token thrift: handing the technical call the mood
    anchors would reintroduce exactly the shared context the split removes.
    """
    dims = list(dims or DIM_KEYS)
    n = len(dims)
    word = _NUMBER_WORDS.get(n, str(n))

    if n == len(DIM_KEYS):
        task_line = f"Score the image on {word} dimensions, each 0-10 with ONE DECIMAL:"
    else:
        task_line = (
            f"Score the image on the following {word} dimension"
            f"{'s' if n > 1 else ''} ONLY, each 0-10 with ONE DECIMAL. Other aspects "
            "of the frame are being judged separately; do not let them influence "
            "these scores, and do not report them:"
        )

    guidance: list[str] = []
    if any(d in dims for d in ("light_color_character", "atmosphere_impact", "composition_framing")):
        guidance.append(_REFERENCE_CLASS)
    if "deliverable_subject" in dims:
        guidance.append(_SUBJECT_LADDER)
    guidance_block = ("\n" + "\n\n".join(guidance) + "\n") if guidance else ""

    if n > 1:
        spread_line = (
            f"The {word} dimensions must not all share the same value. A frame is "
            "normally strong on some axes and weak on others; make that explicit."
        )
    else:
        spread_line = "Score this single dimension on its own merits."

    return (
        _SYSTEM_PROMPT.replace("{task_line}", task_line)
        .replace("{dim_block}", _dim_block(dims))
        .replace("{guidance_block}", guidance_block)
        .replace("{spread_line}", spread_line)
        .replace("{schema_block}", _schema_block(dims))
    )


def prompt_fingerprint(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# scoring math
# --------------------------------------------------------------------------- #


def normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    weights = {k: float(raw.get(k, 0.0)) for k in DIM_KEYS}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("dimension weights sum to zero")
    return {k: v / total for k, v in weights.items()}


def rank_keep_map(
    rows: list[dict[str, Any]], top_pct: float = DEFAULT_KEEP_TOP_PCT
) -> tuple[dict[str, bool], float | None]:
    """Suggest ``keep`` for the top ``top_pct`` of rows by ``overall``.

    Asking a single-image call to decide delivery produced a 70% keep rate against
    a 33% human baseline (kappa 0.166), because the model cannot see the rest of
    the burst. Ranking the pool is at least self-consistent. The consequence worth
    remembering: keep then carries no information beyond ``overall``, so a reviewer
    has to override it deliberately rather than confirm it by reflex.

    Returns ``(key -> keep, threshold_score)``.
    """
    scored = [
        (normalize_name(str(r.get("file"))), float(r["overall"]))
        for r in rows
        if r.get("overall") is not None and r.get("file")
    ]
    if not scored:
        return {}, None
    ranked = sorted(scored, key=lambda t: -t[1])
    n_keep = max(0, min(len(ranked), round(len(ranked) * max(0.0, min(1.0, top_pct)))))
    threshold = ranked[n_keep - 1][1] if n_keep else None
    keepers = {k for k, _ in ranked[:n_keep]}
    return {k: (k in keepers) for k, _ in scored}, threshold


def derive_overall(
    dims: dict[str, float], weights: dict[str, float]
) -> tuple[float, str | None]:
    """Weighted 0-100 blend of the eight dimensions, with a hard technical gate.

    Returns ``(overall, gate_reason)``; ``gate_reason`` is set when the cap bound
    the score so review and reporting can see why.
    """
    missing = [k for k in DIM_KEYS if dims.get(k) is None]
    if missing:
        raise ValueError(f"missing dimensions: {', '.join(missing)}")
    blend = sum(float(dims[k]) * weights[k] for k in DIM_KEYS) * 10.0
    gate: str | None = None
    for k in GATE_DIMS:
        if float(dims[k]) <= GATE_THRESHOLD and blend > GATE_CAP:
            gate = f"{k}<={GATE_THRESHOLD:g} caps overall at {GATE_CAP:g}"
            blend = GATE_CAP
            break
    return round(blend, 1), gate


# --------------------------------------------------------------------------- #
# image + api
# --------------------------------------------------------------------------- #


def encode_image(path: Path, max_edge: int, quality: int) -> tuple[str, int]:
    """Downscale to ``max_edge`` and return ``(data_url, payload_bytes)``.

    Previews are ~1600px; shrinking cuts image tokens (and cost) with no visible
    effect on the judgement being asked for.
    """
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_edge / float(max(w, h)))
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
    raw = buf.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii"), len(raw)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first balanced JSON object out of a model reply."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```")[1] if len(s.split("```")) > 1 else s
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start = s.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start : i + 1])
    raise ValueError("unbalanced JSON object in reply")


def _clamp(v: Any, lo: float, hi: float) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x))


_INTL_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def explain_http_error(status: int, body: str, model: str, base_url: str) -> str:
    """Turn a DashScope 4xx into the specific thing the operator has to go fix."""
    region = "international" if "intl" in base_url else "mainland China"
    other = DEFAULT_BASE_URL if "intl" in base_url else _INTL_BASE_URL
    lines = [f"DashScope rejected the request (HTTP {status}) for model {model!r}.", ""]
    if status == 401:
        lines += [
            "401 means the key itself was not accepted. Check for a stale value, a",
            "copy/paste with whitespace, or a key from a different Alibaba account.",
        ]
    elif status == 403:
        lines += [
            "403 access_denied means the key is recognised but not entitled to this",
            f"model on the {region} endpoint. In likelihood order:",
            "",
            f"  1. {model} is not activated for this account. Model Studio gates VL",
            "     models separately -- open the console and activate it, then retry.",
            "  2. The account has no credit / free quota left, or is in arrears.",
            "  3. Wrong region. This key may belong to the other site; try:",
            f"       export DASHSCOPE_BASE_URL={other}",
            "  4. The API key is scoped to a workspace that lacks model permission.",
            "",
            "Run `python scripts/eval/relabel_qwen.py models` first -- if that also",
            "fails the problem is account-level, if it succeeds pick a listed model.",
        ]
    elif status == 404:
        lines += [
            f"404 means {model!r} does not exist on this endpoint. List what is",
            "callable with: python scripts/eval/relabel_qwen.py models",
        ]
    elif status == 429:
        lines += ["429 rate limit persisted through retries. Lower --concurrency."]
    else:
        lines += ["Check DASHSCOPE_API_KEY, --model and --base-url."]
    lines += ["", f"endpoint: {base_url}", f"response: {body[:400]}"]
    return "\n".join(lines)


@dataclass
class ApiResult:
    payload: dict[str, Any]
    usage: dict[str, int]
    latency_ms: float
    attempts: int


class QwenScorer:
    """Thin DashScope OpenAI-compatible client (no SDK, retries on transient errors)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.0,
        seed: int | None = 20260625,
        max_retries: int = 4,
        timeout: float = 120.0,
        system_prompt: str | None = None,
    ) -> None:
        import requests

        self._session = requests.Session()
        self._requests = requests
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.seed = seed
        self.max_retries = max_retries
        self.timeout = timeout
        self.system_prompt = system_prompt or build_system_prompt()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def list_models(self) -> list[str]:
        resp = self._session.get(
            f"{self.base_url}/models", headers=self._headers(), timeout=self.timeout
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                explain_http_error(resp.status_code, resp.text, "(model list)", self.base_url)
            )
        data = resp.json().get("data") or []
        return sorted(str(m.get("id")) for m in data if m.get("id"))

    def probe(self, model: str) -> tuple[int, str]:
        """Cheapest possible call against ``model``: is it callable at all?

        A text-only one-token request answers the entitlement question without
        paying for image tokens, which is what a 403 actually needs to resolve.
        """
        try:
            resp = self._session.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            return 0, str(exc)[:120]
        if resp.status_code < 400:
            return resp.status_code, "OK"
        try:
            err = (resp.json().get("error") or {}).get("code") or ""
        except Exception:  # noqa: BLE001
            err = ""
        return resp.status_code, err or resp.text[:120]

    def score(self, data_url: str, system_prompt: str | None = None) -> ApiResult:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt or self.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": _USER_PROMPT},
                    ],
                },
            ],
        }
        if self.seed is not None:
            body["seed"] = self.seed

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            started = time.monotonic()
            try:
                resp = self._session.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                    timeout=self.timeout,
                )
                if resp.status_code in RETRY_STATUS:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                if resp.status_code >= 400:
                    # 4xx here is a configuration problem; retrying just burns quota.
                    raise SystemExit(
                        explain_http_error(resp.status_code, resp.text, self.model, self.base_url)
                    )
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"no choices in reply: {str(data)[:300]}")
                text = (choices[0].get("message") or {}).get("content") or ""
                if isinstance(text, list):  # some VL builds return content parts
                    text = "".join(
                        part.get("text", "") for part in text if isinstance(part, dict)
                    )
                payload = extract_json_object(text)
                usage_raw = data.get("usage") or {}
                usage = {
                    k: int(usage_raw.get(k) or 0)
                    for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
                return ApiResult(
                    payload=payload,
                    usage=usage,
                    latency_ms=round((time.monotonic() - started) * 1000, 1),
                    attempts=attempt,
                )
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - retry any transient failure
                last_err = exc
                if attempt >= self.max_retries:
                    break
                backoff = min(30.0, 2.0**attempt) + random.uniform(0, 1.0)
                time.sleep(backoff)
        raise RuntimeError(f"scoring failed after {self.max_retries} attempts: {last_err}")


# --------------------------------------------------------------------------- #
# record assembly + checkpointing
# --------------------------------------------------------------------------- #


def merge_group_results(results: dict[str, ApiResult]) -> tuple[dict[str, Any], dict[str, int], float, int]:
    """Fold per-group API results into one payload plus summed cost.

    Dimension keys cannot collide because ``DIM_GROUPS`` partitions them. The
    narrative fields exist once per call, so the perceptual call wins the main
    ``reason`` (it speaks to the frame's merit, which is what a reviewer reads)
    and the technical one is kept alongside rather than discarded.
    """
    payload: dict[str, Any] = {}
    tags: list[str] = []
    confidences: list[float] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    latency = 0.0
    attempts = 0

    for group in ("technical", "perceptual"):
        res = results.get(group)
        if res is None:
            continue
        for k in DIM_GROUPS[group]:
            payload[k] = res.payload.get(k)
        for t in res.payload.get("tags") or []:
            if isinstance(t, str) and t not in tags:
                tags.append(t)
        conf = _clamp(res.payload.get("confidence"), 0, 1)
        if conf is not None:
            confidences.append(conf)
        for k in usage:
            usage[k] += res.usage.get(k, 0)
        latency += res.latency_ms
        attempts = max(attempts, res.attempts)

    perc = results.get("perceptual")
    tech = results.get("technical")
    lead = perc or tech
    if lead is not None:
        payload["strongest_aspect"] = lead.payload.get("strongest_aspect")
        payload["weakest_aspect"] = lead.payload.get("weakest_aspect")
        payload["reason"] = lead.payload.get("reason")
    if tech is not None and perc is not None:
        payload["reason_technical"] = tech.payload.get("reason")
    payload["tags"] = tags
    # Min, not mean: if either call was unsure about this frame, the merged row is.
    payload["confidence"] = min(confidences) if confidences else None
    return payload, usage, round(latency, 1), attempts


def build_record(
    *,
    file: str,
    payload: dict[str, Any],
    weights: dict[str, float],
    model: str,
    prompt_sha: str,
    usage: dict[str, int],
    latency_ms: float,
    attempts: int,
    image_bytes: int,
    scoring_mode: str = "single",
    prompt_shas: dict[str, str] | None = None,
) -> dict[str, Any]:
    dims: dict[str, float] = {}
    for k in DIM_KEYS:
        v = _clamp(payload.get(k), 0, 10)
        if v is None:
            raise ValueError(f"model omitted dimension {k}")
        dims[k] = round(v, 1)
    overall, gate = derive_overall(dims, weights)
    tags = payload.get("tags")
    return {
        "schema_version": SCHEMA_VERSION,
        "file": file,
        "overall": overall,
        "overall_source": "weighted_dims",
        "dims": dims,
        # Deliberately unset: keep is a comparison against the rest of the shoot,
        # which a single-image call cannot make. Derived by rank at review time.
        "keep": None,
        "technical_gate": gate,
        "strongest_aspect": str(payload.get("strongest_aspect") or ""),
        "weakest_aspect": str(payload.get("weakest_aspect") or ""),
        "reason": str(payload.get("reason") or ""),
        "reason_technical": str(payload.get("reason_technical") or ""),
        "tags": [str(t) for t in tags][:12] if isinstance(tags, list) else [],
        "confidence": _clamp(payload.get("confidence"), 0, 1),
        "model": model,
        "scoring_mode": scoring_mode,
        "prompt_sha": prompt_sha,
        "prompt_shas": prompt_shas or {},
        "usage": usage,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "image_bytes": image_bytes,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


class JsonlAppender:
    """Append-only sink so an interrupted run resumes without losing work."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


# Bands mirror the shape of the existing human labels (11 extreme rejects, then a
# dense 60-88 body) so a small sample still spans reject / middle / keeper.
_SAMPLE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("reject", -1.0, 20.0),
    ("low", 20.0, 70.0),
    ("mid", 70.0, 75.0),
    ("high", 75.0, 80.0),
    ("keeper", 80.0, 101.0),
)


def stratified_sample(
    paths: list[Path], *, labels_path: Path | None, n: int, seed: int
) -> list[Path]:
    """Pick ``n`` files spanning the human score range and distinct sessions.

    A head slice of the manifest covers only the first two or three shoots, which
    is the wrong sample for judging whether the model separates rejects from
    keepers. Falls back to spreading across sessions when labels are missing.
    """
    if n >= len(paths):
        return paths
    rng = random.Random(seed)

    def session_of(p: Path) -> str:
        stem = p.name
        return stem[:8] if stem[:8].isdigit() else stem[:4]

    scores: dict[str, float] = {}
    if labels_path is not None and labels_path.exists():
        for lb in load_labels(labels_path):
            if lb.overall is not None:
                scores[lb.key] = float(lb.overall)

    if not scores:
        by_session: dict[str, list[Path]] = {}
        for p in paths:
            by_session.setdefault(session_of(p), []).append(p)
        for group in by_session.values():
            rng.shuffle(group)
        picked: list[Path] = []
        order = sorted(by_session)
        while len(picked) < n and any(by_session[s] for s in order):
            for s in order:
                if by_session[s] and len(picked) < n:
                    picked.append(by_session[s].pop())
        return sorted(picked)

    buckets: dict[str, list[Path]] = {name: [] for name, _, _ in _SAMPLE_BANDS}
    for p in paths:
        score = scores.get(normalize_name(p.name))
        if score is None:
            continue
        for name, lo, hi in _SAMPLE_BANDS:
            if lo < score <= hi or (name == "reject" and score <= hi):
                buckets[name].append(p)
                break

    total = sum(len(v) for v in buckets.values()) or 1
    picked: list[Path] = []
    for name, _, _ in _SAMPLE_BANDS:
        group = buckets[name]
        if not group:
            continue
        want = max(1, round(n * len(group) / total))
        # Spread within the band so one shoot cannot dominate the sample.
        by_session: dict[str, list[Path]] = {}
        for p in group:
            by_session.setdefault(session_of(p), []).append(p)
        for g in by_session.values():
            rng.shuffle(g)
        order = sorted(by_session, key=lambda s: (-len(by_session[s]), s))
        chosen: list[Path] = []
        while len(chosen) < want and any(by_session[s] for s in order):
            for s in order:
                if by_session[s] and len(chosen) < want:
                    chosen.append(by_session[s].pop())
        picked.extend(chosen)

    rng.shuffle(picked)
    return sorted(picked[:n])


def resolve_targets(
    *, images_dir: Path, manifest: Path | None, limit: int | None
) -> list[Path]:
    """Files to score: manifest order when available, else a sorted dir scan."""
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    if manifest is not None and manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        index = {p.name.lower(): p for p in images_dir.rglob("*") if p.suffix.lower() in exts}
        out: list[Path] = []
        for item in items or []:
            name = str((item or {}).get("file") or "")
            hit = index.get(Path(name).name.lower())
            if hit is not None:
                out.append(hit)
            else:
                sys.stderr.write(f"[relabel] manifest file not on disk, skipped: {name}\n")
        if out:
            return out[:limit] if limit else out
    files = sorted(p for p in images_dir.rglob("*") if p.suffix.lower() in exts)
    return files[:limit] if limit else files


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #


def _is_pinned(model: str) -> bool:
    """True for dated snapshots like ``qwen3-vl-plus-2025-12-19``."""
    parts = model.rsplit("-", 3)
    return (
        len(parts) == 4
        and len(parts[1]) == 4
        and parts[1].isdigit()
        and parts[2].isdigit()
        and parts[3].isdigit()
    )


def _api_key(explicit: str | None) -> str:
    key = explicit or os.environ.get("DASHSCOPE_API_KEY") or ""
    if not key:
        raise SystemExit(
            "DASHSCOPE_API_KEY is not set. Export it or pass --api-key.\n"
            "Get one from the Alibaba Cloud Model Studio (Bailian) console."
        )
    return key


def _weights_from_arg(spec: str | None) -> dict[str, float]:
    if not spec:
        return normalize_weights(DIM_WEIGHTS)
    raw: dict[str, float] = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in DIM_WEIGHTS:
            raise SystemExit(f"unknown dimension in --weights: {k}")
        raw[k] = float(v)
    merged = {**DIM_WEIGHTS, **raw}
    return normalize_weights(merged)


def cmd_models(args: argparse.Namespace) -> int:
    scorer = QwenScorer(
        api_key=_api_key(args.api_key), model=args.model, base_url=args.base_url
    )
    print(f"endpoint  {scorer.base_url}")
    print(f"key       length={len(scorer.api_key)} prefix={scorer.api_key[:3]}***\n")

    vl: list[str] = []
    try:
        ids = scorer.list_models()
        # OCR variants transcribe text rather than judge a frame, so skip them.
        vl = [m for m in ids if "-vl" in m and "-ocr" not in m]
        print(f"/models   {len(ids)} listed, {len(vl)} vision-language (OCR excluded)")
        for m in vl:
            tag = " <- default" if m == DEFAULT_MODEL else (" (pinned)" if _is_pinned(m) else "")
            print(f"            {m}{tag}")
    except Exception as exc:  # noqa: BLE001
        print(f"/models   listing unavailable:\n{exc}\n")

    # Listing permission and call permission are separate on DashScope, so probe.
    print("\nprobe (one text token each -- proves entitlement, not just visibility):")
    candidates = list(dict.fromkeys(list(vl) + list(KNOWN_VL_MODELS)))
    ok: list[str] = []
    for m in candidates:
        status, detail = scorer.probe(m)
        flag = "OK  " if status < 400 and status else "FAIL"
        print(f"  [{flag}] {m:<24} HTTP {status or '---'}  {detail}")
        if status and status < 400:
            ok.append(m)

    print()
    if ok:
        print(f"callable: {', '.join(ok)}")
        pinned = [m for m in vl if _is_pinned(m) and m.rsplit("-", 3)[0] in ok]
        if pinned:
            print(
                f"\nFor a labeling run prefer a pinned snapshot such as {pinned[-1]}:\n"
                "a floating alias can be updated mid-pass, which would leave the\n"
                "dataset scored by two different models with no way to tell which."
            )
        if DEFAULT_MODEL not in ok:
            print(f"\nthe default {DEFAULT_MODEL} is not callable -- run score with:")
            print(f"  python scripts/eval/relabel_qwen.py score --sample 20 --model {ok[0]}")
        return 0
    print(
        "No VL model is callable with this key. Since every candidate failed the\n"
        "same way, this is account-level rather than a bad model name: activate\n"
        "the vision models in the Model Studio console, check the balance, and\n"
        "confirm the key belongs to the same site as the endpoint above."
    )
    return 1


def _describe_sample(targets: list[Path], prior_labels: str | None) -> str:
    """One-line composition of a sample so its coverage is visible before spending."""
    sessions = {p.name[:8] if p.name[:8].isdigit() else p.name[:4] for p in targets}
    path = Path(prior_labels).expanduser() if prior_labels else None
    if path is None or not path.exists():
        return f"{len(sessions)} sessions"
    prior = list(load_labels(path))
    scores = {lb.key: lb.overall for lb in prior if lb.overall is not None}
    keeps = {lb.key for lb in prior if lb.keep is True}
    counts: dict[str, int] = {}
    for p in targets:
        score = scores.get(normalize_name(p.name))
        if score is None:
            counts["unlabeled"] = counts.get("unlabeled", 0) + 1
            continue
        for name, lo, hi in _SAMPLE_BANDS:
            if lo < score <= hi or (name == "reject" and score <= hi):
                counts[name] = counts.get(name, 0) + 1
                break
    order = [n for n, _, _ in _SAMPLE_BANDS] + ["unlabeled"]
    parts = [f"{n}={counts[n]}" for n in order if counts.get(n)]
    n_keep = sum(1 for p in targets if normalize_name(p.name) in keeps)
    return (
        f"{len(sessions)} sessions, {n_keep}/{len(targets)} human keepers, "
        f"bands {' '.join(parts)}"
    )


def cmd_score(args: argparse.Namespace) -> int:
    images_dir = Path(args.images).expanduser().resolve()
    if not images_dir.is_dir():
        raise SystemExit(f"images dir not found: {images_dir}")
    manifest = Path(args.manifest).expanduser() if args.manifest else None
    out_path = Path(args.out).expanduser()
    weights = _weights_from_arg(args.weights)
    split = args.scoring_mode == "split2"
    if split:
        group_prompts = {g: build_system_prompt(dims) for g, dims in DIM_GROUPS.items()}
        prompt_shas = {g: prompt_fingerprint(p) for g, p in group_prompts.items()}
        # One fingerprint over both prompts, so the provenance guard treats a change
        # in either group as a different labeling regime.
        system_prompt = "\n\n===GROUP===\n\n".join(
            group_prompts[g] for g in sorted(group_prompts)
        )
        prompt_sha = prompt_fingerprint(system_prompt)
    else:
        group_prompts, prompt_shas = {}, {}
        system_prompt = build_system_prompt()
        prompt_sha = prompt_fingerprint(system_prompt)

    targets = resolve_targets(
        images_dir=images_dir, manifest=manifest, limit=None if args.sample else args.limit
    )
    if not targets:
        raise SystemExit(f"no images found under {images_dir}")
    if args.sample:
        prior = Path(args.prior_labels).expanduser() if args.prior_labels else None
        targets = stratified_sample(
            targets, labels_path=prior, n=args.sample, seed=args.seed or 0
        )

    existing = read_jsonl(out_path)
    done = {normalize_name(str(r.get("file"))) for r in existing}
    if args.overwrite:
        existing, done = [], set()
        if out_path.exists():
            out_path.unlink()
    elif existing:
        stale = _stale_provenance(existing, prompt_sha, args.model)
        if stale and not args.allow_mixed:
            print(
                f"{out_path} already holds {len(existing)} rows scored under a different\n"
                "prompt or model, and resuming would blend two calibrations into one\n"
                "dataset with no record of the split:\n",
                file=sys.stderr,
            )
            for (sha, model), n in sorted(stale.items(), key=lambda kv: -kv[1]):
                print(f"  {n:4d} rows  prompt {sha}  model {model}", file=sys.stderr)
            print(
                f"  ---- current: prompt {prompt_sha}  model {args.model}\n\n"
                "Use --overwrite to rescore from scratch, or --allow-mixed if you\n"
                "genuinely intend a mixed set (each row records its own provenance).",
                file=sys.stderr,
            )
            return 2
    pending = [p for p in targets if normalize_name(p.name) not in done]

    print(f"images         {len(targets)} (already scored: {len(targets) - len(pending)})")
    if args.sample:
        print(f"sample         {_describe_sample(targets, args.prior_labels)}")
    print(f"model          {args.model}{'' if _is_pinned(args.model) else '  (floating alias)'}")
    if split:
        print(
            f"scoring        split2 -- {len(pending)} images x 2 calls = "
            f"{len(pending) * 2} requests"
        )
        for g, dims in DIM_GROUPS.items():
            print(f"               {g:11s} {prompt_shas[g]}  {', '.join(dims)}")
    else:
        print("scoring        single -- all 8 dimensions in one call")
    print(f"prompt sha     {prompt_sha}{'  (over both group prompts)' if split else ''}")
    print(f"out            {out_path}")
    print(f"weights        {', '.join(f'{k}={v:.2f}' for k, v in weights.items())}")
    print(
        "\nNOTE  These are suggestions, not ground truth. Stage3 runs Qwen2-VL, so a\n"
        "      Qwen-VL labeler shares family bias -- review in scripts/review_server.py\n"
        "      before any of this reaches data/eval/labels.jsonl."
    )
    if not _is_pinned(args.model):
        print(
            f"\nNOTE  {args.model} is a floating alias. If it is updated part-way through\n"
            "      the pass, the set ends up labeled by two models with no record of the\n"
            "      split. Prefer a dated snapshot: relabel_qwen.py models lists them.\n"
        )
    else:
        print()
    if args.dry_run:
        sample = pending[0] if pending else targets[0]
        _, nbytes = encode_image(sample, args.max_edge, args.jpeg_quality)
        print(f"dry run: would score {len(pending)} images")
        print(f"         sample {sample.name} -> {nbytes / 1024:.0f} KiB after downscale")
        print(f"         system prompt is {len(system_prompt)} chars")
        return 0
    if not pending:
        print("nothing to do -- every target already has a suggestion.")
        return 0

    scorer = QwenScorer(
        api_key=_api_key(args.api_key),
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        seed=args.seed,
        max_retries=args.max_retries,
        timeout=args.timeout,
        system_prompt=system_prompt,
    )
    sink = JsonlAppender(out_path)
    failures: list[tuple[str, str]] = []
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    counter = {"n": 0}
    counter_lock = threading.Lock()

    def work(path: Path) -> None:
        data_url, nbytes = encode_image(path, args.max_edge, args.jpeg_quality)
        if split:
            # Sequential per image on purpose: the two calls are independent
            # judgements, and the pool already runs several images at once.
            results = {
                g: scorer.score(data_url, system_prompt=group_prompts[g])
                for g in DIM_GROUPS
            }
            payload, usage, latency_ms, attempts = merge_group_results(results)
        else:
            res = scorer.score(data_url)
            payload, usage, latency_ms, attempts = (
                res.payload,
                res.usage,
                res.latency_ms,
                res.attempts,
            )
        record = build_record(
            file=path.name,
            payload=payload,
            weights=weights,
            model=args.model,
            prompt_sha=prompt_sha,
            usage=usage,
            latency_ms=latency_ms,
            attempts=attempts,
            image_bytes=nbytes,
            scoring_mode=args.scoring_mode,
            prompt_shas=prompt_shas,
        )
        sink.append(record)
        with counter_lock:
            counter["n"] += 1
            for k in totals:
                totals[k] += usage.get(k, 0)
            i = counter["n"]
        # Spread is the live health signal: a flat frame means the model is
        # not actually differentiating the dimensions.
        print(
            f"[{i}/{len(pending)}] {path.name}  overall={record['overall']:.1f} "
            f"spread={_dim_spread(record):.1f}"
            f"{'  GATED' if record['technical_gate'] else ''}  {latency_ms:.0f}ms",
            flush=True,
        )

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(work, p): p for p in pending}
        try:
            for fut in as_completed(futures):
                path = futures[fut]
                try:
                    fut.result()
                except SystemExit:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failures.append((path.name, str(exc)))
                    sys.stderr.write(f"[relabel] FAILED {path.name}: {exc}\n")
        except KeyboardInterrupt:
            sys.stderr.write("\n[relabel] interrupted -- progress is on disk, rerun to resume\n")
            return 130

    elapsed = time.monotonic() - started
    print(f"\nscored   {counter['n']}/{len(pending)} in {elapsed:.0f}s")
    print(f"tokens   prompt={totals['prompt_tokens']} completion={totals['completion_tokens']}")
    print("         (per-token price is account-specific; read it off your console)")
    if failures:
        print(f"failed   {len(failures)} -- rerun the same command to retry just these")
        for name, err in failures[:10]:
            print(f"           {name}: {err[:120]}")
    if args.blind_fraction > 0:
        _write_blind_split(out_path, args.blind_fraction, args.seed or 0)
    print(f"\nnext:    python scripts/review_server.py --suggestions {out_path}")
    return 0 if not failures else 1


def _write_blind_split(out_path: Path, fraction: float, seed: int) -> None:
    """Reserve a random subset to be labeled with the AI hidden.

    Without an unanchored slice there is no way to tell later whether a reviewer
    agreed with the model or merely nodded along to it.
    """
    rows = read_jsonl(out_path)
    files = sorted(str(r.get("file")) for r in rows if r.get("file"))
    if not files:
        return
    n = max(1, round(len(files) * min(1.0, fraction)))
    rng = random.Random(seed)
    blind = sorted(rng.sample(files, n))
    dest = out_path.with_name("blind_split.json")
    body = {"seed": seed, "fraction": fraction, "n": n, "files": blind}
    dest.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"blind    {n} files reserved for unanchored labeling -> {dest}")


def cmd_report(args: argparse.Namespace) -> int:
    from scripts.eval.metrics import cohen_kappa, mae, pearson, spearman

    sug_rows = read_jsonl(Path(args.suggestions).expanduser())
    if not sug_rows:
        raise SystemExit(f"no suggestions in {args.suggestions} -- run `score` first")
    sug = {normalize_name(str(r.get("file"))): r for r in sug_rows}

    print(f"suggestions      {len(sug)}")
    model_names = sorted({str(r.get("model")) for r in sug_rows})
    print(f"model(s)         {', '.join(model_names)}")

    overalls = [float(r["overall"]) for r in sug_rows if r.get("overall") is not None]
    if overalls:
        print(
            f"AI overall       n={len(overalls)} unique={len(set(overalls))} "
            f"min={min(overalls):.1f} max={max(overalls):.1f} "
            f"mean={sum(overalls) / len(overalls):.1f}"
        )
    keep_map, threshold = rank_keep_map(sug_rows, args.keep_top_pct)
    n_keep = sum(1 for v in keep_map.values() if v)
    if keep_map:
        print(
            f"keep (by rank)   {n_keep}/{len(keep_map)} ({args.keep_top_pct:.0%} of pool), "
            f"threshold overall >= {threshold:.1f}"
        )
    gated = [r for r in sug_rows if r.get("technical_gate")]
    print(f"gate applied     {len(gated)} frames capped at {GATE_CAP:g}")

    spreads = [_dim_spread(r) for r in sug_rows]
    if spreads:
        print(
            f"dim spread       mean={sum(spreads) / len(spreads):.2f} "
            f"min={min(spreads):.1f} (flat frames carry no information)"
        )
    flat = [r for r in sug_rows if _dim_spread(r) < 1.0]
    if flat:
        print(f"WARNING          {len(flat)} frames have all 8 dims within 1.0 of each other")
    _report_dim_health(sug_rows)

    labels_path = Path(args.labels).expanduser()
    if not labels_path.exists():
        print(f"\n(no prior labels at {labels_path}; skipping agreement)")
        return 0

    prior = {lb.key: lb for lb in load_labels(labels_path)}
    pairs = [(prior[k].overall, sug[k]["overall"]) for k in sug if k in prior]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    print(f"\nvs {labels_path}")
    print(f"matched          {len(pairs)}")
    if len(pairs) >= 3:
        h = [a for a, _ in pairs]
        m = [b for _, b in pairs]
        print(f"spearman         {spearman(h, m):.3f}")
        print(f"pearson          {pearson(h, m):.3f}")
        print(f"mae              {mae(h, m):.2f}")
        print(f"human unique     {len(set(h))} values   AI unique {len(set(m))} values")
    kp = [
        (prior[k].keep, keep_map[k])
        for k in sug
        if k in prior and k in keep_map and isinstance(prior[k].keep, bool)
    ]
    if kp:
        agree = sum(1 for a, b in kp if a == b)
        print(
            f"keep agreement   {agree}/{len(kp)} ({agree / len(kp):.1%})  "
            f"kappa={cohen_kappa([a for a, _ in kp], [b for _, b in kp]):.3f}  "
            "(rank-derived, so this mostly restates the overall correlation)"
        )
    print(
        "\nRead the correlation as a sanity check on the labeling pass, not as a\n"
        "model score: both sides are estimates and the prior human labels are the\n"
        "coarse ones being replaced."
    )
    if args.json:
        payload = {
            "schema_version": "qwen_relabel_report.v1",
            "n_suggestions": len(sug),
            "models": model_names,
            "ai_overall_unique": len(set(overalls)),
            "keep_top_pct": args.keep_top_pct,
            "keep_threshold": threshold,
            "n_keep_by_rank": n_keep,
            "gated": len(gated),
            "flat_dim_rows": len(flat),
            "vs_prior": {
                "labels": str(labels_path),
                "matched": len(pairs),
                "spearman": spearman([a for a, _ in pairs], [b for _, b in pairs])
                if len(pairs) >= 3
                else None,
                "mae": mae([a for a, _ in pairs], [b for _, b in pairs]) if pairs else None,
            },
        }
        stamp_protocol(
            payload,
            labels_path=labels_path,
            predictions_path=Path(args.suggestions),
            extra={"pass": "qwen_relabel", "weights": normalize_weights(DIM_WEIGHTS)},
        )
        Path(args.json).expanduser().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


# Below this, a dimension is effectively a constant: it adds a fixed offset to
# every score instead of separating frames, which silently inflates weak frames.
_MIN_USEFUL_DIM_SD = 0.6

# A dimension that never goes below this across a mixed set has lost its bottom:
# real rejects and merely-average frames stop being distinguishable.
_UNREACHABLE_FLOOR = 4.0


def _report_dim_health(rows: list[dict[str, Any]]) -> None:
    """Flag dimensions that are near-constant, and the dead weight they carry."""
    if len(rows) < 4:
        return
    weights = normalize_weights(DIM_WEIGHTS)
    print("\nper-dimension spread (near-constant dims carry weight but no signal)")
    dead_weight = 0.0
    dead_floor = 0.0
    stuck: list[str] = []
    for k in DIM_KEYS:
        vals = [float(r["dims"][k]) for r in rows if (r.get("dims") or {}).get(k) is not None]
        if len(vals) < 2:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        sd = var**0.5
        flag = ""
        if sd < _MIN_USEFUL_DIM_SD:
            flag = "  <- near-constant"
            dead_weight += weights[k]
            dead_floor += mean * weights[k] * 10.0
        elif min(vals) >= _UNREACHABLE_FLOOR:
            flag = "  <- floor unreachable"
            stuck.append(STAGE3_DIM_LABELS.get(k, k))
        print(
            f"  {STAGE3_DIM_LABELS.get(k, k):<12} w={weights[k]:.2f}  mean={mean:4.1f}  "
            f"sd={sd:4.2f}  range={min(vals):.1f}-{max(vals):.1f}{flag}"
        )
    if dead_weight > 0:
        print(
            f"\n  {dead_weight:.0%} of the weight is near-constant, adding a flat "
            f"~{dead_floor:.1f} points to every\n  frame. That is a floor under weak "
            "frames, not a judgement. Fix the prompt's\n  reference class rather than "
            "the weights: ranking is unaffected, absolute\n  score and any threshold "
            "downstream are not."
        )
    if stuck:
        print(
            f"\n  {', '.join(stuck)} never scored below {_UNREACHABLE_FLOOR:g}. A shoot"
            "\n  containing genuine rejects should reach the bottom of these ladders;"
            "\n  a prompt that only tells the model what NOT to punish will do this."
        )


def _stale_provenance(
    rows: list[dict[str, Any]], prompt_sha: str, model: str
) -> dict[tuple[str, str], int]:
    """Count existing rows whose (prompt, model) pair differs from this run's."""
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        pair = (str(r.get("prompt_sha") or "?"), str(r.get("model") or "?"))
        if pair != (prompt_sha, model):
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def _dim_spread(row: dict[str, Any]) -> float:
    dims = row.get("dims") or {}
    vals = [float(v) for v in dims.values() if isinstance(v, (int, float))]
    return (max(vals) - min(vals)) if vals else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the Stage3 golden set with Qwen-VL on DashScope for human review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Known VL models: {', '.join(KNOWN_VL_MODELS)}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_api_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--api-key", default=None, help="default: $DASHSCOPE_API_KEY")
        p.add_argument("--model", default=DEFAULT_MODEL, help="default: %(default)s")
        p.add_argument(
            "--base-url",
            default=os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            help="OpenAI-compatible endpoint (use dashscope-intl for outside CN)",
        )

    p_models = sub.add_parser("models", help="list models this API key can call")
    add_api_args(p_models)
    p_models.set_defaults(func=cmd_models)

    p_score = sub.add_parser("score", help="score images and append suggestions (resumable)")
    add_api_args(p_score)
    p_score.add_argument("--images", default="data/eval/images")
    p_score.add_argument("--manifest", default="data/eval/manifest.json", help="'' to scan dir")
    p_score.add_argument("--out", default="data/eval/relabel/qwen_suggestions.jsonl")
    p_score.add_argument("--limit", type=int, default=None, help="score only the first N")
    p_score.add_argument(
        "--sample",
        type=int,
        default=None,
        help="score N images spread across score bands and sessions (better than --limit "
        "for judging quality on a small batch); takes precedence over --limit",
    )
    p_score.add_argument(
        "--prior-labels",
        default="data/eval/labels.jsonl",
        help="existing labels used only to stratify --sample; default: %(default)s",
    )
    p_score.add_argument(
        "--scoring-mode",
        choices=("split2", "single"),
        default="split2",
        help=(
            "split2 (default): technical dims and perceptual dims in two separate "
            "calls, so neither anchors the other -- costs 2 calls per image. "
            "single: all 8 in one call, which measured a mean inter-dimension "
            "Spearman of 0.642 (the dimensions collapse into one impression)"
        ),
    )
    p_score.add_argument("--concurrency", type=int, default=4)
    p_score.add_argument("--max-edge", type=int, default=1280, help="downscale long edge")
    p_score.add_argument("--jpeg-quality", type=int, default=85)
    p_score.add_argument("--temperature", type=float, default=0.0)
    p_score.add_argument("--seed", type=int, default=20260625)
    p_score.add_argument("--max-retries", type=int, default=4)
    p_score.add_argument("--timeout", type=float, default=120.0)
    p_score.add_argument("--weights", default=None, help="e.g. moment_peak=0.25,noise_cleanliness=0.02")
    p_score.add_argument(
        "--blind-fraction",
        type=float,
        default=0.15,
        help="share reserved for AI-hidden labeling (0 to disable); default: %(default)s",
    )
    p_score.add_argument("--overwrite", action="store_true", help="discard existing suggestions")
    p_score.add_argument(
        "--allow-mixed",
        action="store_true",
        help="resume even if existing rows used a different prompt or model",
    )
    p_score.add_argument("--dry-run", action="store_true", help="no API calls")
    p_score.set_defaults(func=cmd_score)

    p_report = sub.add_parser("report", help="suggestion stats and agreement with prior labels")
    p_report.add_argument("--suggestions", default="data/eval/relabel/qwen_suggestions.jsonl")
    p_report.add_argument("--labels", default="data/eval/labels.jsonl")
    p_report.add_argument(
        "--keep-top-pct",
        type=float,
        default=DEFAULT_KEEP_TOP_PCT,
        help="share of the pool suggested as keepers; default: %(default)s",
    )
    p_report.add_argument("--json", default=None, help="also write a JSON report here")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
