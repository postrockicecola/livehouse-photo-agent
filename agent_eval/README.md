# Agent Evaluation Harness

This harness evaluates the behavior of the real Gallery Copilot. It builds the
production skill registry, production prompt, configured chat backend, and
`ConversationalAgent`, which invokes the existing LangGraph
`decide → act → answer` graph. It does not mock planning or tool execution.

## Agent Evaluation Philosophy

This harness treats the Agent as a production software system, not as a prompt.
Any change to the planner, prompt, memory, tools, skills, routing, or workflow
graph must preserve versioned behavioral contracts before release.

The release criteria cover five independent concerns:

- **Correctness** — the requested observable state/result was produced.
- **Reliability** — tools succeeded, loops were avoided, and recovery worked.
- **Efficiency** — the workflow did not add unnecessary model or tool steps.
- **Cost** — token and expensive inference budgets remained controlled.
- **Photography quality** — selected images agree with a separate human golden.

A prompt improvement is not accepted merely because an example looks better. It
must improve benchmark results without causing behavior, quality, efficiency,
cost, or runtime regression.

## Architecture

- `cases/` — versioned behavior contracts. Cases describe intent, trajectory,
  result, ranking goldens, and per-case budgets.
- `case_loader.py` — deterministic YAML/JSON loading and early contract
  validation. Bad benchmarks fail before an expensive model run.
- `instrumentation.py` — transparent wrappers around the real `ChatFn` and
  `SkillRegistry`. They capture model and tool spans without forking agent code.
- `metrics.py` — deterministic task, trajectory, runtime, ranking, and failure
  scoring. Ranking math reuses the Stage3 metric implementation.
- `regression.py` — absolute quality gates plus baseline-relative gates.
- `html_report.py` — a self-contained, CI-artifact-friendly report.
- `run_eval.py` — orchestration, artifact writing, and CI exit contract.

The separation mirrors production eval platforms: dataset, execution adapter,
trace, scorers, policy gate, and presentation are independently replaceable.

```text
Benchmark Cases
        |
        v
Evaluation Runner
        |
        +-------------+
        |             |
        v             v
   Trajectory       Metrics
        |             |
        +-------------+
              |
              v
       Regression Gate
              |
              v
         HTML Report
```

## Execution flow

1. Load and validate every selected benchmark.
2. Copy its existing Gallery session fixture into an isolated temporary folder.
3. Build the production gallery skills, memory skills, prompt, and model backend.
4. Run `ConversationalAgent.chat()`, which uses the compiled LangGraph runtime.
5. Capture model calls, planner decisions, tool parameters/outputs, events,
   retries, memory diffs, budgets, latency, and available inference telemetry.
6. Score the case, aggregate metrics, compare the baseline, and classify failures.
7. Write `evaluation_trace.json`, `evaluation_score.json`, and `latest.html`.

The trace uses the agent turn `run_id` as its `trace_id`. `job_id` is populated
when a tool returns one. Gallery chat is not a Celery job, so absent job IDs and
queue waits are recorded as unavailable rather than invented.

The current production `ChatFn` returns text rather than provider usage objects.
Token counts are therefore clearly labeled estimates. Exact provider tokens can
later be added behind the same instrumentation interface without changing cases
or metrics. Private chain-of-thought is never requested or stored; `planner`
contains observable tool decisions and `thought_summary` remains null unless the
runtime exposes a safe summary.

## Run

```bash
python agent_eval/run_eval.py
python agent_eval/run_eval.py --case semantic_drummer_search --no-baseline
python agent_eval/run_eval.py --native-tools
```

The command returns:

- `0` when absolute thresholds and regression thresholds pass
- `1` when a quality or regression gate fails
- `2` when the harness/configuration cannot run

After a reviewed run, create or refresh the baseline:

```bash
python agent_eval/run_eval.py --save-baseline
```

Do not promote a failing run merely to make CI green. Baselines represent an
accepted model, prompt, tool set, fixture set, and runtime configuration.

## Benchmark format

YAML and JSON are supported. A file may hold one object, a list, or `{cases: []}`.

```yaml
- id: select_social_images
  schema_version: agent_behavior_case.v2
  description: Select a social-media shortlist.
  user_input: 帮我挑选10张适合发朋友圈的
  required_behavior:
    final_answer:
      non_empty: true
      all_tools_ok: true
    selected_images:
      count: 10
    state_changes:
      gallery_selection_updated: true
    budgets:
      max_steps: 6
      max_llm_calls: 3
      max_inference_calls: 0
      max_tokens: 6000
      max_latency_ms: 60000
  optional_behavior:
    preferred_tools: [gallery_search, gallery_select]
    target_steps: 4
    target_tool_calls: 2
  session: smoke
```

`required_behavior.final_answer` may be a required reply substring or an object with
`contains_all`, `contains_any`, `not_contains`, `non_empty`, and
`all_tools_ok`. Required behavior may also assert selected-image count, Gallery
state changes, memory keys, forbidden tools, and runtime budgets.

`optional_behavior.preferred_tools` is diagnostic only. A refactor may replace
`gallery_search + gallery_select` with one equivalent skill without failing the
case, provided the observable behavior remains correct. Legacy
`expected_tools` cases are accepted and normalized into this optional field.

`session` reuses `data/eval/agent/sessions/<name>`; alternatively set
`session_path`.

## Photography golden dataset

Selection quality is intentionally stored separately under `golden/`:

```yaml
goldens:
  - case_id: semantic_drummer_search
    session_id: smoke
    user_request: 找鼓手特写
    expected_images: [drum_01.jpg]
    relevance:
      drum_01.jpg: 3
    k: 5
```

The workflow can pass while photography quality fails, or vice versa. Reports
therefore expose separate `workflow_passed` and `quality_passed` fields.
Golden cases calculate Precision@K, Recall@K, NDCG, and Average Precision; MAP
is the mean Average Precision across eligible cases. Optional
`expected_scores: {image: score}` enables Spearman and MAE when tool metadata
contains aligned image scores.

Add stable, representative cases rather than many paraphrases. Use deterministic
routes for CI smoke coverage and real-model semantic cases for nightly/release
gates. Keep each case isolated unless multi-turn memory behavior is specifically
being evaluated.

## Metrics

- **Behavior:** workflow success, intent accuracy, observable state/result
  checks, and non-blocking preferred-tool fit.
- **Trajectory:** steps, tool calls, reflections, retries, duplicate-call loop
  detection, and maximum depth.
- **Runtime:** total/P50/P95 latency, LLM calls, VLM inference calls, estimated
  tokens/cost, and queue wait when a tool exposes it.
- **Quality:** Precision@K, Recall@K, NDCG, MAP, Spearman, and MAE. Metrics without
  eligible ranking/score goldens are null, not zero.

Workflow success is strict about required behavior and budgets, but does not
require an exact tool sequence. Ranking quality remains a separate dimension.

## Composite Agent Score

`evaluation_score.json` contains a configurable 0–100 score:

```json
{
  "overall_score": 91.5,
  "task_success": 98.0,
  "quality": 85.0,
  "cost": 92.0,
  "trajectory": 90.0
}
```

Default weights in `config.yaml` are behavior/task success 40%, photography
quality 30%, cost 15%, and trajectory efficiency 15%. If a dataset has no
eligible quality labels, unavailable weight is redistributed rather than
silently treating missing quality as zero or perfect.

## Decision trace

Each tool step records only observable information:

- observation available to the runtime;
- short system-authored decision purpose and source;
- action/tool parameters;
- summarized result, state change, and latency.

The trace never asks for or stores private chain-of-thought. Model tool calls and
deterministic route IDs are observable decisions; tool-purpose summaries are
defined by the harness.

To add a metric, compute the per-case value in `evaluate_case()`, aggregate it in
`aggregate()`, and only then add a regression policy if the metric is stable
enough to gate releases. This keeps observability metrics separate from policy.

## Regression and failure classification

`config.yaml` contains absolute and baseline-relative tolerances. Regression
results are grouped into Behavior, Quality, Efficiency, Cost, and Runtime
categories, with a direct “System became worse because…” explanation.

Case failures are assigned one primary category and subtype:

`Planning Error`, `Tool Selection Error`, `Tool Parameter Error`,
`Execution Failure`, `Memory Failure`, `Reflection Failure`, `Budget Failure`,
`Ranking Failure`, or `Unknown`.

Classification is deterministic and ordered from infrastructure/control failures
to task-quality failures, making trend counts suitable for CI and dashboards.

## GitHub Actions

Start with deterministic/local infrastructure and a pinned model:

```yaml
- name: Agent regression evaluation
  run: python agent_eval/run_eval.py
- name: Upload agent evaluation
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: agent-evaluation
    path: |
      reports/latest.html
      reports/evaluation_trace.json
      reports/evaluation_score.json
```

Pin the model image/tag, config, fixture data, and baseline. Otherwise backend
drift will be indistinguishable from code regression.

## What this demonstrates in interviews

- How do you test an agent rather than a function? — Replay behavior contracts
  through the real graph and score both outcome and trajectory.
- How do you debug a pass/fail? — Join case, model spans, tool observations,
  memory diffs, runtime budgets, and the turn trace in one artifact.
- How do you prevent efficient-looking but wrong agents? — Gate task success and
  tool/intent correctness alongside latency and token cost.
- How do you handle nondeterminism? — Pin runtime inputs, use tolerances and
  representative cases, and separate smoke gates from broader nightly suites.
- How do you avoid storing chain-of-thought? — Capture observable decisions and
  safe summaries only.
- How do evals become release infrastructure? — Baseline comparison, explicit
  thresholds, machine-readable traces, HTML artifacts, and stable exit codes.

