export type Setting = {
  key: string;
  name: string;
  label?: string;
  group?: string;
  stage?: string;
  section?: string;
  retired?: boolean;
  control?: string;
  default: string | number;
  value?: string | number;
  prompt?: string;
  description?: string;
  choices?: Array<string | number>;
  minimum?: number;
  maximum?: number | null;
  is_override?: boolean;
  workload?: string;
  source?: string;
};
export type ModelRoute = {
  profile: string;
  thinking: string;
  timeout_seconds?: number;
  max_output_tokens?: number;
};
export type Reviewer = ModelRoute & { id: string; name: string; focus: string };
export type FlowConfiguration = {
  version: number;
  models: Record<string, { name: string; model: string }>;
  routes: Record<string, ModelRoute>;
  reviewers: Reviewer[];
  workloads: Record<string, Record<string, ModelRoute>>;
};
export type FlowStage = {
  id: string;
  name: string;
  kind: string;
  inputs: string[];
  outputs: string[];
  x?: number;
  y?: number;
  auxiliary?: boolean;
  inference?: boolean;
};
export type FlowEdge = { source: string; target: string; label: string };
export type LLMSettings = {
  scope: string;
  runtime:
    | (Record<string, unknown> & {
        provider: string;
        model: string;
        model_editable: boolean;
      })
    | null;
  style: { key: string; value: string; default: string; is_override: boolean };
  prompts: Setting[];
  parameters: Setting[];
  flow: {
    key: string;
    revision: string;
    configuration: FlowConfiguration;
    stages: FlowStage[];
    edges: FlowEdge[];
    workloads: Record<string, string>;
  };
  stage_usage?: {
    available: boolean;
    days?: number;
    rows: Array<{
      stage: string;
      workload?: string | null;
      requested_model: string;
      reported_model: string | null;
      attempts: number;
      failures: number;
      unfinished_attempts: number;
      seconds: number;
      input_tokens: number | null;
      output_tokens: number | null;
      unpriced_attempts: number;
      estimated_usd_min: number | null;
      estimated_usd_max: number | null;
    }>;
  };
};
export const thinkingLevels = [
  { value: "none", label: "Off" },
  { value: "low", label: "Low" },
  { value: "high", label: "High" },
  { value: "max", label: "Max" },
];
export function describeRoute(
  route: ModelRoute,
  configuration: FlowConfiguration,
) {
  return `${configuration.models[route.profile]?.name || route.profile} · ${thinkingLevels.find((level) => level.value === route.thinking)?.label || route.thinking}`;
}
