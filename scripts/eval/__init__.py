"""Stage3 scoring evaluation harness (P0 baseline).

Decoupled from the live pipeline: consumes a predictions JSON (e.g.
``analysis_results.json``) plus a human-labeled JSONL ground-truth file and
reports how well the AI scores agree with human judgement.

Submodules:
- :mod:`scripts.eval.metrics` — pure-numpy correlation / error / ranking metrics.
- :mod:`scripts.eval.labels` — label schema, prediction loading, filename join.

CLI entrypoint: :mod:`scripts.eval_stage3`.
"""
