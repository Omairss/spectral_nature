export type UserContext = {
  user_id: string;
  email: string;
  display_name: string;
  first_name: string;
  last_name: string;
  role: string;
  portfolio_id: string;
  portfolio_slug: string;
  portfolio_name: string;
  membership_role: string;
  share_fraction: number;
  can_view_full_portfolio: boolean;
};

export type DatasetResponse<T = unknown> = {
  request: { operation: "dataset"; name: string; params: Record<string, unknown> };
  result_type: "dataset";
  payload: T;
  provenance: null | {
    mode: string;
    datasets: string[];
    details: Record<string, unknown>;
  };
  messages: string[];
};

export type PortfolioView = {
  status: "available" | "unavailable";
  provider: "robinhood_main" | "alpaca";
  period: string;
  portfolio: { id: string; name: string; share_fraction: number };
  summary: Record<string, unknown>;
  holdings: Array<Record<string, unknown> & { asset_class?: string; symbol?: string; description?: string }>;
  timeseries: Array<Record<string, unknown> & { timestamp?: string; portfolio?: number }>;
  equity_analytics: {
    positions: Array<Record<string, unknown> & { symbol?: string }>;
    correlation: { labels: string[]; values: Array<Array<number | null>> };
    components: Array<Record<string, unknown> & { symbol?: string }>;
    stats: Record<string, unknown>;
  };
  performance: { metrics: Record<string, unknown>; table: Array<Record<string, unknown>> };
  account_performance: Array<Record<string, unknown>>;
  opportunity_sets: Array<Record<string, unknown>>;
  refresh: Record<string, unknown>;
  collector_errors: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  can_manage: boolean;
  execution_enabled: boolean;
  as_of: string;
};

export type Session = {
  authenticated: boolean;
  context?: UserContext;
};

export type HomeBeat = {
  id: string;
  kind: string;
  title: string;
  summary: string;
  business_context: string;
  symbols: string[];
};

export type HomeView = {
  status: "available" | "unavailable";
  view_date: string;
  is_replay: boolean;
  as_of: string;
  run_id: string;
  summary: null | {
    headline: string;
    markdown: string;
    status: string;
    featured_symbols: string[];
    audio_available: boolean;
    audio_mime_type: string;
  };
  graph: VisualizationArtifact | null;
  beats: HomeBeat[];
  coverage: Record<string, number>;
  provenance: { mode: string; datasets: string[] };
};

export type StockInvestigatorView = {
  status: "available" | "unavailable";
  ticker: string;
  days: number;
  company: {
    name: string;
    business_lens: string;
    headline: string;
    summary: string;
    why_now: string;
    management_signal: string;
    background: string;
    as_of: string;
  };
  technical: Record<string, number | string | null>;
  forecast: Record<string, number | string | null | Record<string, number | string | null>>;
  articles: Array<{
    headline: string;
    source: string;
    published_at: string;
    summary: string;
    url: string;
  }>;
  unavailable: string[];
  provenance: { modes: string[]; datasets: string[] };
};

export type ResearchCitation = { url: string; label: string };

export type ResearchResult = {
  status: string;
  answer_markdown: string;
  confidence: string;
  limitations: string[];
  citations: ResearchCitation[];
  provider: string;
  model: string;
  evidence_pack_id: string;
  error: string;
};

export type ResearchRun = {
  run_id: string;
  surface: string;
  task: string;
  query: string;
  metadata: { parent_run_id?: string } & Record<string, unknown>;
  status: "queued" | "running" | "cancel_requested" | "completed" | "failed" | "cancelled";
  result: ResearchResult | null;
  last_event_sequence: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  cancel_requested_at: string | null;
  attempt_count: number;
  dispatch_status: "pending" | "dispatched" | "failed";
  error_code: string;
  write_policy: "none";
};

export type ResearchEvent = {
  event_id: string;
  run_id: string;
  sequence: number;
  event_type: string;
  stage: string;
  message: string;
  progress: number;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ResearchEvents = {
  run_id: string;
  events: ResearchEvent[];
  last_event_sequence: number;
};

export type DatasetColumn = {
  name: string;
  logical_type: "string" | "number" | "boolean" | "array" | "object" | "mixed" | "unknown";
};

export type DatasetArtifact = {
  schema_version: "dataset.v1";
  dataset_id: string;
  name: string;
  delivery: "inline";
  row_count: number;
  columns: DatasetColumn[];
  records: Record<string, unknown>[];
};

export type VisualizationArtifact = {
  schema_version: "visualization.v1";
  artifact_type: "visualization";
  artifact_id: string;
  title: string;
  renderer: "plotly" | "vega-lite" | "perspective" | "sigma" | "deck-gl";
  renderer_contract: string;
  dataset_refs: string[];
  datasets: Record<string, DatasetArtifact>;
  spec: {
    figure?: {
      data?: unknown[];
      layout?: Record<string, unknown>;
    };
    config?: Record<string, unknown>;
  };
  linked_selection_group: string;
  provenance: {
    mode: string;
    datasets: string[];
    details: Record<string, unknown>;
  };
  performance: {
    row_count: number;
    encoded_bytes: number;
    delivery: string;
    level_of_detail: string;
  };
};

export type VisualizationResponse = {
  request: { operation: "visualization"; name: string; params: Record<string, unknown> };
  result_type: "visualization";
  payload: VisualizationArtifact;
  provenance: VisualizationArtifact["provenance"];
  messages: string[];
};
