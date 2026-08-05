"""Self-contained production-style Agent evaluation dashboard."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _h(value: Any) -> str:
    return html.escape(str(value))


def _json(value: Any) -> str:
    return _h(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _card(label: str, value: Any, detail: str = "", tone: str = "") -> str:
    return (
        f'<div class="card {tone}"><span>{_h(label)}</span><strong>{_h(value)}</strong>'
        f"<small>{_h(detail)}</small></div>"
    )


def _bar(label: str, value: float, maximum: float, suffix: str = "") -> str:
    width = 0 if maximum <= 0 else min(100.0, value / maximum * 100.0)
    return (
        f'<div class="bar-row"><span>{_h(label)}</span><div class="bar-bg">'
        f'<i style="width:{width:.2f}%"></i></div><b>{value:.1f}{_h(suffix)}</b></div>'
    )


def _decision_steps(steps: list[dict[str, Any]]) -> str:
    return "".join(
        f"""
        <article class="decision-step">
          <b>Step {_h(step.get('step'))}</b>
          <div><label>Observation</label><pre>{_json(step.get('observation'))}</pre></div>
          <div><label>Decision summary</label><pre>{_json(step.get('decision'))}</pre></div>
          <div><label>Action</label><pre>{_json(step.get('action'))}</pre></div>
          <div><label>Result</label><pre>{_json(step.get('result'))}</pre></div>
        </article>"""
        for step in steps
    )


def _case_detail(row: dict[str, Any]) -> str:
    case = row["case"]
    execution = row["execution"]
    metrics = row["metrics"]
    workflow_passed = bool(metrics.get("workflow_passed"))
    quality = metrics.get("quality") or {}
    failure = metrics.get("failure")
    status = "pass" if workflow_passed and failure is None else "fail"
    timeline: list[tuple[float, str, str]] = []
    for call in execution.get("llm_calls") or []:
        timeline.append(
            (
                float(call.get("started_at") or 0),
                f"LLM #{call.get('index')}",
                f"{float(call.get('latency_ms') or 0):.0f} ms",
            )
        )
    for call in execution.get("tool_spans") or []:
        timeline.append(
            (
                float(call.get("started_at") or 0),
                str(call.get("tool") or "tool"),
                f"{float(call.get('latency_ms') or 0):.0f} ms",
            )
        )
    timeline.sort()
    quality_label = (
        "not evaluated"
        if not quality.get("eligible")
        else "PASS"
        if quality.get("passed")
        else "FAIL"
    )
    return f"""
    <details class="case {status}" {'open' if status == 'fail' else ''}>
      <summary>
        <b>{_h(case['id'])}</b>
        <span>{_h(case['description'])}</span>
        <em>Workflow {'PASS' if workflow_passed else 'FAIL'} · Quality {_h(quality_label)}</em>
      </summary>
      <div class="case-body">
        <section class="case-grid">
          <div><h4>User Input</h4><pre>{_h(case['user_input'])}</pre></div>
          <div><h4>Final Answer</h4><pre>{_h(execution.get('reply') or execution.get('error') or '')}</pre></div>
          <div><h4>Expected Behavior</h4><pre>{_json(case.get('required_behavior'))}</pre></div>
          <div><h4>Actual Behavior</h4><pre>{_json(metrics.get('actual_behavior'))}</pre></div>
          <div><h4>Optional Preferences</h4><pre>{_json(case.get('optional_behavior'))}</pre></div>
          <div><h4>Behavior Checks</h4><pre>{_json(metrics.get('behavior_checks'))}</pre></div>
        </section>
        <h4>Decision Trace</h4>
        <div class="decision-trace">{_decision_steps(execution.get('decision_trace') or [])}</div>
        <section class="case-grid">
          <div><h4>Tool Timeline</h4>{''.join(
              f'<p class="timeline"><b>{_h(kind)}</b><span>{_h(detail)}</span></p>'
              for _, kind, detail in timeline
          ) or '<p class="muted">No tool/model spans.</p>'}</div>
          <div><h4>Metrics</h4><pre>{_json(metrics)}</pre></div>
          <div><h4>Photography Golden</h4><pre>{_json(quality)}</pre></div>
          <div><h4>Failure Reason</h4><pre>{_json(failure or {'status': 'none'})}</pre></div>
        </section>
      </div>
    </details>"""


def render_report(report: dict[str, Any], output: Path) -> None:
    metrics = report["metrics"]
    behavior = metrics.get("behavior") or {}
    runtime = metrics.get("runtime") or {}
    trajectory = metrics.get("trajectory") or {}
    quality = metrics.get("quality") or {}
    score = report.get("score") or {}
    results = report["cases"]
    regression = report["regression"]
    failures = [
        row for row in results if isinstance((row.get("metrics") or {}).get("failure"), dict)
    ]
    latencies = [
        (str(row["case"]["id"]), float(row["execution"].get("latency_ms") or 0))
        for row in results
    ]
    failure_counts = metrics.get("failure_counts") or {}
    max_latency = max([value for _, value in latencies] or [1.0])
    max_failures = max(list(failure_counts.values()) or [1])
    regression_explanations = "".join(
        f"<li><b>{_h(row.get('category'))}</b>: {_h(row.get('explanation'))}</li>"
        for row in regression.get("regressions") or []
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Livehouse Agent Evaluation</title><style>
:root{{--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;--muted:#8b949e;--blue:#58a6ff;--green:#3fb950;--red:#f85149;--amber:#d29922}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}}
main{{max-width:1280px;margin:auto;padding:28px}} header{{display:flex;justify-content:space-between;gap:20px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:20px}}
h1{{margin:0;font-size:25px}} h2{{margin:30px 0 12px}} h4{{margin:12px 0 7px}} .muted,small{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:18px 0}}
.card,.panel,details{{background:var(--panel);border:1px solid var(--line);border-radius:8px}} .card{{padding:14px}} .card span,.card small{{display:block}}
.card strong{{display:block;font-size:24px;margin:4px 0}} .card.good strong{{color:var(--green)}} .card.bad strong{{color:var(--red)}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}} .panel{{padding:16px}}
.bar-row{{display:grid;grid-template-columns:170px 1fr 90px;gap:10px;align-items:center;margin:9px 0}} .bar-bg{{height:9px;background:#21262d;border-radius:5px;overflow:hidden}}
.bar-bg i{{display:block;height:100%;background:var(--blue)}} .bar-row b{{text-align:right}}
details{{margin:10px 0;overflow:hidden}} summary{{display:grid;grid-template-columns:210px 1fr 250px;gap:12px;cursor:pointer;padding:13px 16px}}
summary em{{text-align:right;color:var(--green)}} .fail summary em{{color:var(--red)}} .case-body{{border-top:1px solid var(--line);padding:16px}}
.case-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}} pre{{white-space:pre-wrap;word-break:break-word;background:#0d1117;padding:10px;border-radius:6px;max-height:320px;overflow:auto;margin:0}}
.decision-trace{{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px}} .decision-step{{min-width:310px;max-width:380px;border:1px solid var(--line);border-radius:7px;padding:12px}}
.decision-step label{{display:block;color:var(--muted);font-size:11px;margin:7px 0 3px;text-transform:uppercase}} .decision-step pre{{max-height:130px;font-size:12px}}
.timeline{{display:flex;justify-content:space-between;border-left:3px solid var(--blue);padding:7px 10px;margin:8px 0}} .good{{color:var(--green)}} .bad{{color:var(--red)}} .warning{{color:var(--amber)}}
@media(max-width:800px){{.grid,.case-grid{{grid-template-columns:1fr}} summary{{grid-template-columns:1fr}} header{{display:block}}}}
</style></head><body><main>
<header><div><h1>Livehouse Agent Evaluation</h1><div class="muted">{_h(report['run']['run_id'])}</div></div>
<div class="muted">{_h(report['run']['started_at'])} · {_h(report['run']['model'].get('model_name'))}</div></header>

<h2>Overview</h2>
<section class="cards">
{_card('Overall Agent Score', score.get('overall_score', '—'), '0–100 configurable composite', 'good' if float(score.get('overall_score') or 0) >= 80 else 'bad')}
{_card('Release Gate', 'PASS' if report['passed'] else 'FAIL', 'Behavior + score + regression', 'good' if report['passed'] else 'bad')}
{_card('Behavior Success', _pct(behavior.get('success_rate')), f"{behavior.get('passed', 0)}/{behavior.get('total', 0)} contracts")}
{_card('Quality Score', score.get('quality') if score.get('quality') is not None else '—', f"{quality.get('eligible_cases', 0)} golden cases")}
{_card('Trajectory Score', score.get('trajectory', '—'), 'steps / tools / loops')}
{_card('Cost Score', score.get('cost', '—'), runtime.get('token_source') or '')}
</section>

<div class="grid">
<section class="panel"><h2>Behavior</h2>
{_bar('Success rate', float(behavior.get('success_rate') or 0)*100, 100, '%')}
{_bar('Intent accuracy', float(behavior.get('intent_accuracy') or 0)*100, 100, '%')}
{_bar('Preferred tool fit', float(behavior.get('preferred_tool_score') or 0)*100, 100, '%')}
<p class="muted">Preferred tools are informational; required observable behavior determines workflow pass/fail.</p></section>

<section class="panel"><h2>Trajectory</h2>
{_card('Average steps', trajectory.get('average_steps'))}
{_card('Average tool calls', trajectory.get('average_tool_calls'))}
{_card('Loops', trajectory.get('loop_count'))}
{_card('Reflections / retries', f"{trajectory.get('reflection_count')} / {trajectory.get('retry_count')}")}</section>

<section class="panel"><h2>Runtime</h2>
{_card('P50 / P95 latency', f"{float(runtime.get('p50_latency_ms') or 0):.0f} / {float(runtime.get('p95_latency_ms') or 0):.0f} ms")}
{_card('Average tokens', runtime.get('average_tokens'), runtime.get('token_source') or '')}
{_card('Average LLM / inference calls', f"{runtime.get('average_llm_calls')} / {runtime.get('average_inference_calls')}")}
{''.join(_bar(case_id, value, max_latency, ' ms') for case_id, value in latencies)}</section>

<section class="panel"><h2>Photography Quality</h2>
{_card('Precision@K', _pct(quality.get('precision_at_k')))}
{_card('Recall@K', _pct(quality.get('recall_at_k')))}
{_card('NDCG', _pct(quality.get('ndcg')))}
{_card('MAP', _pct(quality.get('map')))}
<p class="muted">Photography quality is reported separately from workflow correctness.</p></section>

<section class="panel"><h2>Failure Analysis</h2>
{''.join(_bar(category, float(count), float(max_failures)) for category,count in failure_counts.items()) or '<p class="good">No classified failures.</p>'}
{''.join(f"<p class='bad'><b>{_h(row['case']['id'])}</b>: {_h(row['metrics']['failure']['category'])} / {_h(row['metrics']['failure']['subtype'])}</p>" for row in failures)}</section>

<section class="panel"><h2>Regression Gate</h2>
<p class="{'good' if regression.get('passed') else 'bad'}">{_h(regression.get('summary'))}</p>
<ul>{regression_explanations or '<li>No blocking regression detected.</li>'}</ul>
<pre>{_json({'categories': regression.get('categories'), 'deltas': regression.get('deltas'), 'threshold_failures': report.get('threshold_failures')})}</pre></section>
</div>

<h2>Per Case Details</h2>
{''.join(_case_detail(row) for row in results)}
</main></body></html>""",
        encoding="utf-8",
    )

