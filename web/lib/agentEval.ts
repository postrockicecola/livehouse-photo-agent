export type AgentEvalModel = {
  provider?: string;
  model_name?: string;
  native_tools?: boolean;
};

export type AgentEvalRun = {
  run_id: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
  model?: AgentEvalModel;
  case_count?: number;
};

export type AgentEvalCase = {
  id: string;
  description: string;
  user_input: string;
  passed: boolean;
  workflow_passed: boolean;
  expected_intent?: string | null;
  actual_intent?: string | null;
  tools: string[];
  steps: number;
  tool_calls: number;
  llm_calls: number;
  latency_ms: number;
  tokens: number;
  trajectory_score?: number | null;
  quality_score?: number | null;
  failed_checks: string[];
  failure?: {
    category?: string;
    subtype?: string;
    reason?: string;
  } | null;
  trace_id?: string | null;
};

export type AgentEvalReport = {
  schema_version?: string;
  run: AgentEvalRun;
  metrics: {
    behavior?: {
      total?: number;
      passed?: number;
      success_rate?: number;
      intent_accuracy?: number;
      preferred_tool_score?: number;
    };
    trajectory?: {
      average_steps?: number;
      average_tool_calls?: number;
      reflection_count?: number;
      retry_count?: number;
      loop_count?: number;
      max_depth?: number;
      trajectory_score?: number;
    };
    runtime?: {
      total_latency_ms?: number;
      p50_latency_ms?: number;
      p95_latency_ms?: number;
      average_llm_calls?: number;
      average_tokens?: number;
      average_cost_usd?: number;
      token_source?: string;
      cost_score?: number;
    };
    quality?: {
      eligible_cases?: number;
      precision_at_k?: number | null;
      recall_at_k?: number | null;
      ndcg?: number | null;
      map?: number | null;
      quality_score?: number | null;
    };
    failure_counts?: Record<string, number>;
  };
  score: {
    overall_score?: number;
    task_success?: number;
    quality?: number;
    cost?: number;
    trajectory?: number;
  };
  thresholds?: Record<string, number>;
  threshold_failures: string[];
  regression?: {
    available?: boolean;
    passed?: boolean;
    regressions?: string[];
    deltas?: Record<string, number | null>;
    summary?: string;
  };
  passed: boolean;
  cases?: AgentEvalCase[];
};

export type AgentEvalHistoryItem = {
  run_id: string;
  started_at?: string;
  completed_at?: string;
  passed: boolean;
  overall_score?: number | null;
  behavior_success_rate?: number | null;
  intent_accuracy?: number | null;
  quality_score?: number | null;
  trajectory_score?: number | null;
  p95_latency_ms?: number | null;
  average_tokens?: number | null;
  model?: AgentEvalModel;
  case_count?: number;
  threshold_failure_count?: number;
  regression_count?: number;
};

export type AgentEvalHistory = {
  schema_version?: string;
  count: number;
  runs: AgentEvalHistoryItem[];
};
