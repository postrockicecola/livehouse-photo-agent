"""Pydantic v2 schemas for structured VLM output validation — Stage 3 (fast / full) and Stage 4.

These models replace fragile hand-rolled coercion with declarative field validators and
type constraints.  The legacy ``dict`` contract consumed by downstream pipeline stages is
preserved via each model's ``to_parsed_dict()`` / ``to_parsed_list()`` method so callers
need no changes.

Usage (structured output path with instructor):
    from inference.schemas import Stage3FullResponse
    import instructor, openai

    client = instructor.from_openai(openai.OpenAI(base_url=..., api_key=...))
    result: Stage3FullResponse = client.chat.completions.create(
        model=model_name, response_model=Stage3FullResponse, messages=[...]
    )

Usage (Pydantic-only validation layer on existing JSON string):
    from inference.schemas import Stage3FullResponse
    from pydantic import ValidationError

    try:
        validated = Stage3FullResponse.model_validate_json(raw_json)
        parsed = validated.to_parsed_dict()
    except ValidationError:
        parsed = legacy_fallback_parser(raw_json)
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from utils.stage3_dimensions import STAGE3_DIM_KEYS

# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

_DimScore = Annotated[float, Field(ge=0.0, le=10.0)]


def _coerce_dim(v: Any) -> float:
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return 5.0


class BilingualText(BaseModel):
    """Bilingual string pair ``{zh, en}``.  Missing side mirrors the populated side."""

    zh: str = ""
    en: str = ""

    @model_validator(mode="after")
    def _mirror_empty(self) -> "BilingualText":
        if self.zh and not self.en:
            self.en = self.zh
        elif self.en and not self.zh:
            self.zh = self.en
        return self

    @classmethod
    def from_any(cls, v: Any) -> "BilingualText":
        """Coerce raw VLM output (dict or plain string) into a BilingualText."""
        if isinstance(v, dict):
            return cls(
                zh=str(v.get("zh") or "").strip(),
                en=str(v.get("en") or "").strip(),
            )
        s = str(v or "").strip()
        return cls(zh=s, en=s)


# ---------------------------------------------------------------------------
# Stage 3 — fast pass
# ---------------------------------------------------------------------------


class Stage3FastResponse(BaseModel):
    """Compact fast-pass VLM output: aggregate score (0–100), bilingual verdict, tags."""

    score: float = Field(default=55.0, ge=0.0, le=100.0)
    verdict: BilingualText = Field(default_factory=BilingualText)
    tags: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, v: Any) -> float:
        try:
            return max(0.0, min(100.0, float(v)))
        except (TypeError, ValueError):
            return 55.0

    @field_validator("verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, v: Any) -> Any:
        return v if isinstance(v, BilingualText) else BilingualText.from_any(v)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(t).strip()[:80] for t in v if t is not None and str(t).strip()]

    def to_parsed_dict(self) -> dict[str, Any]:
        return {"score": self.score, "verdict": self.verdict.model_dump(), "tags": self.tags}


# ---------------------------------------------------------------------------
# Stage 3 — full 8-dimension pass
# ---------------------------------------------------------------------------


class Stage3FullResponse(BaseModel):
    """Full 8-dimension VLM rubric output.

    Dimension scores are inlined at the JSON top level (not nested under ``dimensions``),
    matching the VLM prompt contract in ``stage3_dimensions.py``.  ``to_parsed_dict``
    re-nests them for downstream pipeline compatibility.
    """

    focus_sharpness: _DimScore = 5.0
    exposure_control: _DimScore = 5.0
    noise_cleanliness: _DimScore = 5.0
    composition_framing: _DimScore = 5.0
    light_color_character: _DimScore = 5.0
    moment_peak: _DimScore = 5.0
    atmosphere_impact: _DimScore = 5.0
    deliverable_subject: _DimScore = 5.0

    strongest_aspect: BilingualText = Field(default_factory=BilingualText)
    weakest_aspect: BilingualText = Field(default_factory=BilingualText)
    tags: list[str] = Field(default_factory=list)
    # Optional per-dimension text comments (may be absent in fast-model outputs).
    comments: dict[str, Any] = Field(default_factory=dict)
    editing_suggestions: list[BilingualText] = Field(default_factory=list)

    @field_validator(
        "focus_sharpness",
        "exposure_control",
        "noise_cleanliness",
        "composition_framing",
        "light_color_character",
        "moment_peak",
        "atmosphere_impact",
        "deliverable_subject",
        mode="before",
    )
    @classmethod
    def _clamp_dim(cls, v: Any) -> float:
        return _coerce_dim(v)

    @field_validator("strongest_aspect", "weakest_aspect", mode="before")
    @classmethod
    def _coerce_bilingual(cls, v: Any) -> Any:
        return v if isinstance(v, BilingualText) else BilingualText.from_any(v)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(t).strip() for t in v if t is not None and str(t).strip()]

    @field_validator("editing_suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, v: Any) -> list[Any]:
        if not isinstance(v, list):
            return []
        out: list[BilingualText] = []
        for x in v[:5]:
            b = BilingualText.from_any(x)
            if b.zh or b.en:
                out.append(b)
        return out

    def to_parsed_dict(self) -> dict[str, Any]:
        """Emit the legacy ``dict`` contract expected by downstream pipeline stages."""
        dims = {k: float(getattr(self, k)) for k in STAGE3_DIM_KEYS}
        dim_comments: dict[str, dict[str, str]] = {}
        for k in STAGE3_DIM_KEYS:
            raw = self.comments.get(k)
            if raw:
                b = BilingualText.from_any(raw)
                if b.zh or b.en:
                    dim_comments[k] = b.model_dump()
        return {
            "dimensions": dims,
            "strongest_aspect": self.strongest_aspect.model_dump(),
            "weakest_aspect": self.weakest_aspect.model_dump(),
            "tags": self.tags,
            "dimension_comments": dim_comments,
            "editing_suggestions": [s.model_dump() for s in self.editing_suggestions],
        }


# ---------------------------------------------------------------------------
# Stage 4 — editing suggestions
# ---------------------------------------------------------------------------


class Stage4EditingResponse(BaseModel):
    """Stage 4 VLM output: a list of bilingual Lightroom-style editing action items."""

    editing_suggestions: list[BilingualText] = Field(default_factory=list)

    @field_validator("editing_suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, v: Any) -> list[Any]:
        if not isinstance(v, list):
            return []
        out: list[BilingualText] = []
        for x in v[:5]:
            b = BilingualText.from_any(x)
            if b.zh or b.en:
                out.append(b)
        return out

    def to_parsed_list(self) -> list[dict[str, str]]:
        return [s.model_dump() for s in self.editing_suggestions]
