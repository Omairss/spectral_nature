"use client";

import dynamic from "next/dynamic";
import type { VisualizationArtifact } from "@/lib/types";
import type { VisualizationSelection } from "./plotly-renderer";

const PlotlyRenderer = dynamic(
  () => import("./plotly-renderer").then((module) => module.PlotlyRenderer),
  { ssr: false, loading: () => <div className="visualization-loading" aria-label="Loading visualization" /> },
);

type VisualizationProps = {
  artifact: VisualizationArtifact;
  onSelection?: (selection: VisualizationSelection) => void;
};

export function Visualization({ artifact, onSelection }: VisualizationProps) {
  if (artifact.schema_version !== "visualization.v1" || artifact.artifact_type !== "visualization") {
    return <div className="visualization-error" role="alert">Visualization contract is unsupported.</div>;
  }
  if (artifact.renderer === "plotly" && artifact.renderer_contract === "plotly.figure.v1") {
    return <PlotlyRenderer artifact={artifact} onSelection={onSelection} />;
  }
  return <div className="visualization-error" role="alert">Visualization renderer is unavailable.</div>;
}
