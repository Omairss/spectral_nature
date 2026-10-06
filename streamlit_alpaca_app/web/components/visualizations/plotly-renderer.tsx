"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Plot from "react-plotly.js";
import type { VisualizationArtifact } from "@/lib/types";

export type VisualizationSelection = {
  group: string;
  renderer: string;
  pointIndexes: number[];
};

type PlotlyRendererProps = {
  artifact: VisualizationArtifact;
  onSelection?: (selection: VisualizationSelection) => void;
};

export function PlotlyRenderer({ artifact, onSelection }: PlotlyRendererProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);
  const figure = artifact.spec.figure;
  const data = Array.isArray(figure?.data) ? figure.data : [];

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const update = (width: number) => setCompact(width < 560);
    update(frame.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => update(entries[0]?.contentRect.width || frame.clientWidth));
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(
    () => {
      const source = figure?.layout || {};
      const sourceTitle = typeof source.title === "object" && source.title ? source.title : { text: source.title };
      const sourceLegend = typeof source.legend === "object" && source.legend ? source.legend : {};
      const sourceXAxis = typeof source.xaxis === "object" && source.xaxis ? source.xaxis : {};
      const sourceYAxis = typeof source.yaxis === "object" && source.yaxis ? source.yaxis : {};
      return {
        ...source,
        autosize: true,
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(8,12,17,0.72)",
        font: { color: "#aeb8c2", family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 11 },
        margin: source.margin || (compact ? { l: 44, r: 12, t: 60, b: 48 } : { l: 58, r: 24, t: 52, b: 48 }),
        title: compact ? { ...sourceTitle, font: { size: 14 }, x: 0, xanchor: "left" } : sourceTitle,
        legend: compact
          ? {
              ...sourceLegend,
              bgcolor: "rgba(8,12,17,0.82)",
              bordercolor: "rgba(39,49,61,0.72)",
              borderwidth: 1,
              font: { size: 9 },
              x: 0.98,
              xanchor: "right",
              y: 0.98,
              yanchor: "top",
            }
          : sourceLegend,
        xaxis: compact ? { ...sourceXAxis, nticks: 4, title: { text: "" } } : sourceXAxis,
        yaxis: compact ? { ...sourceYAxis, nticks: 5, title: { text: "" } } : sourceYAxis,
        uirevision: artifact.artifact_id,
      };
    },
    [artifact.artifact_id, compact, figure?.layout],
  );
  const config = useMemo(
    () => {
      const source = artifact.spec.config || {};
      const requestedRemovals = Array.isArray(source.modeBarButtonsToRemove) ? source.modeBarButtonsToRemove : [];
      return {
        ...source,
        displaylogo: false,
        modeBarButtonsToRemove: [...requestedRemovals, "sendChartToCloud", "sendDataToCloud", "editInChartStudio"],
        responsive: true,
        scrollZoom: false,
        showLink: false,
        showSendToCloud: false,
      };
    },
    [artifact.spec.config],
  );

  return (
    <div className="plotly-frame" ref={frameRef}>
      <Plot
        className="plotly-renderer"
        config={config}
        data={data}
        layout={layout}
        onSelected={(event: { points?: Array<{ pointIndex?: number }> }) => {
          if (!onSelection) return;
          onSelection({
            group: artifact.linked_selection_group,
            renderer: artifact.renderer,
            pointIndexes: (event?.points || [])
              .map((point) => point.pointIndex)
              .filter((value): value is number => typeof value === "number"),
          });
        }}
        useResizeHandler
      />
    </div>
  );
}
