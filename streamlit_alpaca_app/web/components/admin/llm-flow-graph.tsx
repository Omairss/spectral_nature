"use client";

import type { FlowConfiguration, FlowEdge, FlowStage } from "./llm-flow-types";
import { describeRoute } from "./llm-flow-types";
import styles from "./llm-settings.module.css";

export function LLMFlowGraph({
  stages,
  edges,
  configuration,
  selected,
  onSelect,
}: {
  stages: FlowStage[];
  edges: FlowEdge[];
  configuration: FlowConfiguration;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const visible = stages.filter((stage) => !stage.auxiliary);
  function path(edge: FlowEdge, index: number) {
    const source = visible.find((stage) => stage.id === edge.source)!;
    const target = visible.find((stage) => stage.id === edge.target)!;
    const sx = (source.x || 0) * 10 + 80,
      sy = (source.y || 0) * 4 + 36;
    const tx = (target.x || 0) * 10 + 80,
      ty = (target.y || 0) * 4 + 36;
    if (edge.source === "researcher" && edge.target === "synthesis")
      return `M${sx + 80},${sy} C${sx + 130},${sy + 80} ${tx - 130},${ty + 80} ${tx - 80},${ty}`;
    if (Math.abs(sy - ty) < 10 && sx < tx)
      return `M${sx + 80},${sy} L${tx - 80},${ty}`;
    if (Math.abs(sy - ty) < 10) {
      const lane = 120 + (index % 2) * 18;
      return `M${sx},${sy - 36} V${lane} Q${sx},${lane - 10} ${sx - 10},${lane - 10} H${tx + 10} Q${tx},${lane - 10} ${tx},${lane} V${ty - 36}`;
    }
    return `M${sx},${sy + (ty > sy ? 36 : -36)} C${sx},${(sy + ty) / 2} ${tx},${(sy + ty) / 2} ${tx},${ty + (ty > sy ? -36 : 36)}`;
  }
  return (
    <>
      <div className={styles.graph} aria-label="Research data flow">
        <svg
          viewBox="0 0 1080 400"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <defs>
            <marker
              id="llm-flow-arrow"
              markerWidth="7"
              markerHeight="7"
              refX="6"
              refY="3.5"
              orient="auto"
            >
              <path d="M0,0 L6,3.5 L0,7" />
            </marker>
          </defs>
          {edges.map((edge, index) => (
            <path
              className={
                edge.source === selected || edge.target === selected
                  ? styles.activeEdge
                  : undefined
              }
              key={`${edge.source}-${edge.target}`}
              d={path(edge, index)}
              markerEnd="url(#llm-flow-arrow)"
            >
              <title>{edge.label}</title>
            </path>
          ))}
        </svg>
        <div className={styles.evidenceGroup}>
          <span>Evidence lookup · live search + retained sources</span>
        </div>
        {visible.map((stage) => (
          <button
            type="button"
            key={stage.id}
            className={`${styles.node} ${stage.kind === "Evidence lookup" ? styles.evidenceNode : ""}`}
            aria-pressed={selected === stage.id}
            style={{ left: `${(stage.x || 0) / 1.08}%`, top: `${stage.y}%` }}
            onClick={() => onSelect(stage.id)}
          >
            <strong>
              {stage.name}
              {stage.id === "review"
                ? ` · ${configuration.reviewers.length}`
                : ""}
            </strong>
            <small>
              {stage.inference === false
                ? "Render completed answer"
                : stage.id === "review"
                  ? configuration.reviewers
                      .map(
                        (reviewer) =>
                          configuration.models[reviewer.profile]?.name,
                      )
                      .join(" + ") || "Off"
                  : describeRoute(
                      configuration.routes[stage.id],
                      configuration,
                    )}
            </small>
          </button>
        ))}
      </div>
      <div className={styles.mobileSteps}>
        {stages.map((stage) => (
          <button
            type="button"
            aria-pressed={selected === stage.id}
            key={stage.id}
            onClick={() => onSelect(stage.id)}
          >
            {stage.name}
          </button>
        ))}
      </div>
    </>
  );
}
