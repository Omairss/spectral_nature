"use client";

import { useMemo } from "react";
import type { VisualizationArtifact } from "@/lib/types";
import { Visualization } from "./visualization";

type DataPlotProps = {
  id: string;
  title: string;
  data: unknown[];
  layout?: Record<string, unknown>;
  rowCount?: number;
  displayModeBar?: boolean;
};

export function DataPlot({ id, title, data, layout = {}, rowCount = 0, displayModeBar = true }: DataPlotProps) {
  const artifact = useMemo<VisualizationArtifact>(() => ({
    schema_version: "visualization.v1",
    artifact_type: "visualization",
    artifact_id: id,
    title,
    renderer: "plotly",
    renderer_contract: "plotly.figure.v1",
    dataset_refs: [],
    datasets: {},
    spec: {
      figure: { data, layout: { title: { text: title }, ...layout } },
      config: { displayModeBar },
    },
    linked_selection_group: id,
    provenance: { mode: "application-view", datasets: [], details: {} },
    performance: {
      row_count: rowCount,
      encoded_bytes: 0,
      delivery: "inline",
      level_of_detail: "full",
    },
  }), [data, displayModeBar, id, layout, rowCount, title]);
  return <Visualization artifact={artifact} />;
}
