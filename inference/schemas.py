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
    """Clamp a raw VLM dimension to ``[0, 10]``; unparseable values become 5.0.

    Example::

        _coerce_dim(8.2)      # -> 8.2
        _coerce_dim("12")     # -> 10.0  (high clip)
        _coerce_dim(-1)       # -> 0.0
        _coerce_dim("n/a")    # -> 5.0   (neutral fallback)
    """
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return 5.0


class BilingualText(BaseModel):
    """Bilingual string pair ``{zh, en}``.  Missing side mirrors the populated side.

    Example::

        BilingualText(zh="构图稳", en="")
        # -> BilingualText(zh="构图稳", en="构图稳")
    """

    zh: str = ""
    en: str = ""

    @model_validator(mode="after")
    def _mirror_empty(self) -> "BilingualText":
        """Copy the non-empty side onto the empty side after construction.

        Example::

            BilingualText(zh="", en="peak gesture")
            # -> zh="peak gesture", en="peak gesture"
            BilingualText(zh="高光略爆", en="slight highlight clip")
            # -> unchanged (both sides already set)
        """
        if self.zh and not self.en:
            self.en = self.zh
        elif self.en and not self.zh:
            self.zh = self.en
        return self

    @classmethod
    def from_any(cls, v: Any) -> "BilingualText":
        """Coerce raw VLM output (dict or plain string) into a BilingualText.

        Example::

            BilingualText.from_any({"zh": "构图稳", "en": "solid framing"})
            # -> BilingualText(zh="构图稳", en="solid framing")
            BilingualText.from_any("peak moment")
            # -> BilingualText(zh="peak moment", en="peak moment")
            BilingualText.from_any({"zh": "  闭眼  "})
            # -> BilingualText(zh="闭眼", en="闭眼")  (strip + mirror)
        """
        if isinstance(v, dict):
            return cls(
                zh=str(v.get("zh") or "").strip(),
                en=str(v.get("en") or "").strip(),
            )
        s = str(v or "").strip()
        return cls(zh=s, en=s)


class SemanticGateObservation(BaseModel):
    """Model observation only; deterministic reject policy is applied downstream.

    Example::

        SemanticGateObservation.model_validate({
            "is_present": True,
            "types": ["no_clear_subject"],
            "severity": "2.4",
            "confidence": 0.88,
            "evidence": "no identifiable performer",
        })
        # -> types=["no_clear_subject"], severity=2, confidence=0.88
    """

    is_present: bool = False
    types: list[str] = Field(default_factory=list)
    severity: int = Field(default=0, ge=0, le=3)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str = Field(default="", max_length=500)

    @field_validator("types", mode="before")
    @classmethod
    def _coerce_types(cls, v: Any) -> list[str]:
        """Strip / truncate type strings; non-lists become ``[]``.

        Example::

            _coerce_types(["  closed_eyes  ", "", None])  # -> ["closed_eyes"]
            _coerce_types("no_clear_subject")             # -> []
        """
        return _coerce_str_tags(v, max_len=80)

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, v: Any) -> int:
        """Round to int and clamp to ``0–3``.

        Example::

            _coerce_severity("2.6")   # -> 3
            _coerce_severity(9)       # -> 3
            _coerce_severity("bad")   # -> 0
        """
        try:
            return max(0, min(3, int(round(float(v)))))
        except (TypeError, ValueError):
            return 0

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: Any) -> float:
        """Parse and clamp confidence to ``[0, 1]``.

        Example::

            _coerce_confidence("0.91")  # -> 0.91
            _coerce_confidence(1.4)     # -> 1.0
            _coerce_confidence(None)    # -> 0.0
        """
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# Stage 3 — fast pass
# ---------------------------------------------------------------------------


def _coerce_str_tags(v: Any, *, max_len: int = 80) -> list[str]:
    """Keep non-empty list items as short strings; anything else is ``[]``.

    Example::

        _coerce_str_tags(["  backlight haze  ", "", None, "x" * 100], max_len=8)
        # -> ["backligh", "xxxxxxxx"]   (strip, drop empty, then [:max_len])
        _coerce_str_tags("performer")  # -> []
    """
    if not isinstance(v, list):
        return []
    return [str(t).strip()[:max_len] for t in v if t is not None and str(t).strip()]


class Stage3FastResponse(BaseModel):
    """Compact fast-pass VLM output: aggregate score (0–100), bilingual verdict, tags.

    Example::

        Stage3FastResponse.model_validate({
            "score": "82",
            "verdict": "peak gesture under red gel",
            "tags": ["red gel", "peak motion"],
            "mood_tags": ["热烈"],
        }).to_parsed_dict()
        # -> {"score": 82.0,
        #     "verdict": {"zh": "peak gesture under red gel",
        #                 "en": "peak gesture under red gel"},
        #     "tags": ["red gel", "peak motion"], "mood_tags": ["热烈"],
        #     "dimensions": {}, "semantic_gate": None}
    """

    score: float = Field(default=55.0, ge=0.0, le=100.0)
    verdict: BilingualText = Field(default_factory=BilingualText)
    tags: list[str] = Field(default_factory=list)
    mood_tags: list[str] = Field(default_factory=list)
    dimensions: dict[str, float] = Field(default_factory=dict)
    semantic_gate: SemanticGateObservation | None = None

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, v: Any) -> float:
        """Clamp fast score to ``[0, 100]``; junk becomes 55.0.

        Example::

            _coerce_score("91")   # -> 91.0
            _coerce_score(140)    # -> 100.0
            _coerce_score("?")    # -> 55.0
        """
        try:
            return max(0.0, min(100.0, float(v)))
        except (TypeError, ValueError):
            return 55.0

    @field_validator("verdict", mode="before")
    @classmethod
    def _coerce_verdict(cls, v: Any) -> Any:
        """Accept a BilingualText, a ``{zh,en}`` dict, or a plain string.

        Example::

            _coerce_verdict("strong silhouette")
            # -> BilingualText(zh="strong silhouette", en="strong silhouette")
            _coerce_verdict(BilingualText(zh="剪影", en="silhouette"))  # -> unchanged
        """
        return v if isinstance(v, BilingualText) else BilingualText.from_any(v)

    @field_validator("tags", "mood_tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        """Same as :func:`_coerce_str_tags` (empty / non-list → ``[]``).

        Example::

            _coerce_tags(["  pit  ", ""])  # -> ["pit"]
        """
        return _coerce_str_tags(v, max_len=80)

    def to_parsed_dict(self) -> dict[str, Any]:
        """Flatten to the dict ``deep_analysis`` / parsers already consume.

        Example::

            Stage3FastResponse(score=70, verdict=BilingualText(zh="可用", en="usable")
            ).to_parsed_dict()["verdict"]
            # -> {"zh": "可用", "en": "usable"}
        """
        return {
            "score": self.score,
            "verdict": self.verdict.model_dump(),
            "tags": self.tags,
            "mood_tags": self.mood_tags,
            "dimensions": self.dimensions,
            "semantic_gate": (
                self.semantic_gate.model_dump() if self.semantic_gate is not None else None
            ),
        }


# ---------------------------------------------------------------------------
# Stage 3 — full 8-dimension pass
# ---------------------------------------------------------------------------


class Stage3FullResponse(BaseModel):
    """Full 8-dimension VLM rubric output.

    Dimension scores are inlined at the JSON top level (not nested under ``dimensions``),
    matching the VLM prompt contract in ``stage3_dimensions.py``.  ``to_parsed_dict``
    re-nests them for downstream pipeline compatibility.

    Example::

        Stage3FullResponse.model_validate({
            "dimensions": {"composition_framing": 8.5, "deliverable_subject": 3},
            "strongest_aspect": "tight framing",
            "tags": ["red gel"],
        }).to_parsed_dict()["dimensions"]["composition_framing"]
        # -> 8.5   (nested Route-B key lifted; other dims default 5.0)
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
    mood_tags: list[str] = Field(default_factory=list)
    semantic_gate: SemanticGateObservation | None = None
    # Optional per-dimension text comments (may be absent in fast-model outputs).
    comments: dict[str, Any] = Field(default_factory=dict)
    editing_suggestions: list[BilingualText] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_nested_dimensions(cls, value: Any) -> Any:
        """Copy ``dimensions.{key}`` up to the top level when the key is missing.

        Example::

            _accept_nested_dimensions({
                "dimensions": {"focus_sharpness": 7},
                "tags": ["haze"],
            })
            # -> {"dimensions": {"focus_sharpness": 7},
            #     "focus_sharpness": 7, "tags": ["haze"]}
            _accept_nested_dimensions("not a dict")  # -> "not a dict"
        """
        if not isinstance(value, dict):
            return value
        nested = value.get("dimensions")
        if not isinstance(nested, dict):
            return value
        merged = dict(value)
        for key in STAGE3_DIM_KEYS:
            if key not in merged and key in nested:
                merged[key] = nested[key]
        return merged

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
        """Same clamp as :func:`_coerce_dim` (used on all eight score fields).

        Example::

            _clamp_dim("9.2")  # -> 9.2
            _clamp_dim(None)   # -> 5.0
        """
        return _coerce_dim(v)

    @field_validator("strongest_aspect", "weakest_aspect", mode="before")
    @classmethod
    def _coerce_bilingual(cls, v: Any) -> Any:
        """Same as :meth:`BilingualText.from_any` unless already a model.

        Example::

            _coerce_bilingual({"zh": "瞬间准"})
            # -> BilingualText(zh="瞬间准", en="瞬间准")
        """
        return v if isinstance(v, BilingualText) else BilingualText.from_any(v)

    @field_validator("tags", "mood_tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        """Same as :func:`_coerce_str_tags`.

        Example::

            _coerce_tags(["silhouette", "  "])  # -> ["silhouette"]
        """
        return _coerce_str_tags(v, max_len=80)

    @field_validator("editing_suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, v: Any) -> list[Any]:
        """Keep up to 5 non-empty bilingual items; non-lists become ``[]``.

        Example::

            _coerce_suggestions(["压高光", {"zh": "加对比", "en": "add contrast"}, ""])
            # -> [BilingualText(zh="压高光", en="压高光"),
            #     BilingualText(zh="加对比", en="add contrast")]
            _coerce_suggestions("not a list")  # -> []
        """
        if not isinstance(v, list):
            return []
        out: list[BilingualText] = []
        for x in v[:5]:
            b = BilingualText.from_any(x)
            if b.zh or b.en:
                out.append(b)
        return out

    def to_parsed_dict(self) -> dict[str, Any]:
        """Emit the legacy ``dict`` contract expected by downstream pipeline stages.

        Example::

            Stage3FullResponse(composition_framing=8.0).to_parsed_dict()["dimensions"]
            # -> eight keys, composition_framing=8.0, the rest 5.0
            # also: strongest_aspect/weakest_aspect as {zh,en},
            #       dimension_comments, editing_suggestions, semantic_gate
        """
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
            "mood_tags": self.mood_tags,
            "semantic_gate": (
                self.semantic_gate.model_dump() if self.semantic_gate is not None else None
            ),
            "dimension_comments": dim_comments,
            "editing_suggestions": [s.model_dump() for s in self.editing_suggestions],
        }


# ---------------------------------------------------------------------------
# Stage 4 — editing suggestions
# ---------------------------------------------------------------------------


class Stage4EditingResponse(BaseModel):
    """Stage 4 VLM output: a list of bilingual Lightroom-style editing action items.

    Example::

        Stage4EditingResponse.model_validate({
            "editing_suggestions": ["压高光", {"zh": "降饱和", "en": "desaturate"}],
        }).to_parsed_list()
        # -> [{"zh": "压高光", "en": "压高光"},
        #     {"zh": "降饱和", "en": "desaturate"}]
    """

    editing_suggestions: list[BilingualText] = Field(default_factory=list)

    @field_validator("editing_suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, v: Any) -> list[Any]:
        """Same 5-item bilingual cap as :meth:`Stage3FullResponse._coerce_suggestions`.

        Example::

            _coerce_suggestions(["压高光"])  # -> [BilingualText(zh="压高光", en="压高光")]
        """
        if not isinstance(v, list):
            return []
        out: list[BilingualText] = []
        for x in v[:5]:
            b = BilingualText.from_any(x)
            if b.zh or b.en:
                out.append(b)
        return out

    def to_parsed_list(self) -> list[dict[str, str]]:
        """Return ``[{zh, en}, ...]`` for Stage4 callers.

        Example::

            Stage4EditingResponse().to_parsed_list()  # -> []
        """
        return [s.model_dump() for s in self.editing_suggestions]
