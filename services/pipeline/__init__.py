"""Pipeline entry points (fast Stage1+2-only runs, etc.)."""

from .fast_pipeline import FastPipelineResult, run_fast_pipeline

__all__ = ["FastPipelineResult", "run_fast_pipeline"]
