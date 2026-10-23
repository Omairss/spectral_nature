"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { VisualizationArtifact } from "@/lib/types";
import type { VisualizationSelection } from "./plotly-renderer";

const PlotlyRenderer = dynamic(
  () => import("./plotly-renderer").then((module) => module.PlotlyRenderer),
  { ssr: false, loading: () => <div className="visualization-loading" aria-label="Loading visualization" /> },
);

type VisualizationProps = {
  artifact: VisualizationArtifact;
  onSelection?: (selection: VisualizationSelection) => void;
  showTitle?: boolean;
  preserveAxisTitles?: boolean;
};

export function Visualization({ artifact, onSelection, showTitle = true, preserveAxisTitles = false }: VisualizationProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element || visible) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setVisible(true);
        observer.disconnect();
      },
      { rootMargin: "360px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [visible]);

  if (artifact.schema_version !== "visualization.v1" || artifact.artifact_type !== "visualization") {
    return <div className="visualization-error" role="alert">Visualization contract is unsupported.</div>;
  }
  if (artifact.renderer === "plotly" && artifact.renderer_contract === "plotly.figure.v1") {
    return (
      <div className="visualization-viewport" ref={viewportRef}>
        {visible ? <PlotlyRenderer artifact={artifact} onSelection={onSelection} showTitle={showTitle} preserveAxisTitles={preserveAxisTitles} /> : <div className="visualization-deferred" />}
      </div>
    );
  }
  return <div className="visualization-error" role="alert">Visualization renderer is unavailable.</div>;
}
