"use client";

import { useState } from "react";
import { responseError, secureMutation } from "@/lib/client/api";
import { LLMFlowGraph } from "./llm-flow-graph";
import {
  describeRoute,
  thinkingLevels,
  type LLMSettings,
  type ModelRoute,
  type Reviewer,
  type Setting,
} from "./llm-flow-types";
import styles from "./llm-settings.module.css";
export type { LLMSettings } from "./llm-flow-types";
type Change = string | number | null;

export function LLMSettingsEditor({
  settings,
  onSaved,
}: {
  settings: LLMSettings;
  onSaved: (settings: LLMSettings) => void;
}) {
  const [configuration, setConfiguration] = useState(
    settings.flow.configuration,
  );
  const [changes, setChanges] = useState<Record<string, Change>>({});
  const [stageId, setStageId] = useState("researcher");
  const [workload, setWorkload] = useState("interactive");
  const [tab, setTab] = useState("flow");
  const [detail, setDetail] = useState("settings");
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const stage = settings.flow.stages.find((item) => item.id === stageId)!;
  const configurationChanged =
    JSON.stringify(configuration) !==
    JSON.stringify(settings.flow.configuration);
  const patch = {
    ...changes,
    ...(configurationChanged
      ? { [settings.flow.key]: JSON.stringify(configuration) }
      : {}),
  };
  const count = Object.keys(patch).length;
  const usedProfiles = new Set(
    [...Object.values(configuration.routes), ...Object.values(configuration.workloads || {}).flatMap(Object.values), ...configuration.reviewers].map(
      (route) => route.profile,
    ),
  );
  const legacyKeys = new Set([
    "param:agent_answer_judge_enabled",
    "param:trajectory_monitor_reasoning_effort",
  ]);
  const parameters = settings.parameters.filter(
    (item) =>
      item.stage === stageId && !item.retired && !legacyKeys.has(item.key),
  );
  const visibleParameters = parameters.filter((item) => !item.workload || item.workload === workload);
  const effectiveRoutes = {
    ...configuration.routes,
    ...(configuration.workloads?.[workload] || {}),
  };
  const prompts = settings.prompts.filter((item) => item.stage === stageId);
  const others = [...settings.prompts, ...settings.parameters].filter(
    (item) => !item.stage && !item.retired && !legacyKeys.has(item.key),
  );
  const usage =
    settings.stage_usage?.rows.filter((row) => row.stage === stageId && row.workload === workload) || [];
  const otherGroups = Array.from(
    new Set(others.map((item) => item.group || "Other")),
  );

  function change(key: string, next: Change, active: unknown) {
    setNotice("");
    setChanges((previous) => {
      const result = { ...previous };
      if (next === active) delete result[key];
      else result[key] = next;
      return result;
    });
  }
  function value(key: string, active: unknown, fallback: unknown) {
    return key in changes ? (changes[key] ?? fallback) : active;
  }
  function routeChange(id: string, update: Partial<ModelRoute>) {
    setNotice("");
    setConfiguration((previous) => ({
      ...previous,
      workloads: {
        ...previous.workloads,
        [workload]: {
          ...previous.workloads?.[workload],
          [id]: { ...(previous.workloads?.[workload]?.[id] || previous.routes[id]), ...update },
        },
      },
    }));
  }
  function reviewerChange(id: string, update: Partial<Reviewer>) {
    setNotice("");
    setConfiguration((previous) => ({
      ...previous,
      reviewers: previous.reviewers.map((reviewer) =>
        reviewer.id === id ? { ...reviewer, ...update } : reviewer,
      ),
    }));
  }
  function inheritRoute(id: string) {
    setConfiguration((previous) => {
      const routes = { ...previous.workloads?.[workload] };
      delete routes[id];
      return { ...previous, workloads: { ...previous.workloads, [workload]: routes } };
    });
  }
  function routeSummary(route: ModelRoute, config = configuration) {
    return `${describeRoute(route, config)} · timeout ${route.timeout_seconds || "shared"} · tokens ${route.max_output_tokens || "default"}`;
  }
  function selectStage(id: string) {
    setStageId(id);
    setDetail("settings");
  }
  function discard() {
    setChanges({});
    setConfiguration(settings.flow.configuration);
    setReviewing(false);
    setNotice("");
  }
  async function save() {
    setSaving(true);
    setError("");
    try {
      const response = await secureMutation("/api/admin/llm", {
        method: "PATCH",
        body: JSON.stringify({ changes: patch }),
      });
      if (!response.ok)
        throw new Error(
          await responseError(response, "LLM settings could not be saved."),
        );
      const saved = (await response.json()) as LLMSettings;
      onSaved(saved);
      setConfiguration(saved.flow.configuration);
      setChanges({});
      setReviewing(false);
      setNotice("Saved for new runs.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "LLM settings could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }
  function routeFields(
    route: ModelRoute,
    update: (update: Partial<ModelRoute>) => void,
    id: string,
  ) {
    return (
      <div className={styles.fields}>
        <label>
          Model
          <select
            aria-label={`${id} model`}
            value={route.profile}
            onChange={(event) => update({ profile: event.target.value })}
          >
            {Object.entries(configuration.models).map(([key, model]) => (
              <option key={key} value={key}>
                {model.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Thinking
          <select
            aria-label={`${id} thinking`}
            value={route.thinking}
            onChange={(event) => update({ thinking: event.target.value })}
          >
            {thinkingLevels.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    );
  }
  function limits(
    route: ModelRoute,
    update: (update: Partial<ModelRoute>) => void,
  ) {
    return (
      <details className={styles.advanced}>
        <summary>Model limits</summary>
        <div className={styles.fields}>
          <label>
            Request timeout (seconds)
            <input
              type="number"
              min={0}
              max={1800}
              step={1}
              value={route.timeout_seconds || 0}
              onChange={(event) =>
                update({ timeout_seconds: Number(event.target.value) })
              }
            />
            <small>0 uses the shared timeout.</small>
          </label>
          <label>
            Output token allowance
            <input
              type="number"
              min={0}
              max={262144}
              step={1}
              value={route.max_output_tokens || 0}
              onChange={(event) =>
                update({ max_output_tokens: Number(event.target.value) })
              }
            />
            <small>0 uses the automatic allowance.</small>
          </label>
        </div>
      </details>
    );
  }
  function settingField(item: Setting) {
    const active = item.prompt ?? item.value;
    return (
      <div className={styles.setting} key={item.key}>
        <label
          className={item.control === "toggle" ? styles.toggle : undefined}
        >
          {item.label || item.name}
          {item.control === "toggle" ? (
            <input
              aria-label={item.label || item.name}
              type="checkbox"
              checked={Number(value(item.key, active, item.default)) !== 0}
              onChange={(event) =>
                change(item.key, event.target.checked ? 1 : 0, active)
              }
            />
          ) : item.prompt !== undefined ? (
            <textarea
              aria-label={item.label || item.name}
              rows={9}
              value={String(value(item.key, active, item.default) ?? "")}
              onChange={(event) => change(item.key, event.target.value, active)}
            />
          ) : item.choices?.length ? (
            <select
              aria-label={item.label || item.name}
              value={String(value(item.key, active, item.default))}
              onChange={(event) =>
                change(
                  item.key,
                  typeof item.default === "number"
                    ? Number(event.target.value)
                    : event.target.value,
                  active,
                )
              }
            >
              {item.choices.map((choice) => (
                <option value={String(choice)} key={String(choice)}>
                  {String(choice).replaceAll("_", " ")}
                </option>
              ))}
            </select>
          ) : (
            <input
              aria-label={item.label || item.name}
              type={typeof item.default === "number" ? "number" : "text"}
              min={item.minimum ?? 0}
              max={item.maximum ?? undefined}
              step={Number.isInteger(item.default) ? 1 : "any"}
              value={String(value(item.key, active, item.default) ?? "")}
              onChange={(event) =>
                change(
                  item.key,
                  typeof item.default === "number"
                    ? Number(event.target.value)
                    : event.target.value,
                  active,
                )
              }
            />
          )}
        </label>
        {item.source === "environment" || item.source === "worker" ? <small>Worker setting</small> : null}
        <button
          className={styles.textButton}
          type="button"
          onClick={() => change(item.key, null, active)}
        >
          Use default
        </button>
      </div>
    );
  }
  const diffs = [
    ...Object.keys(settings.flow.workloads).flatMap((key) =>
      settings.flow.stages.filter((stage) =>
        JSON.stringify(configuration.workloads?.[key]?.[stage.id]) !== JSON.stringify(settings.flow.configuration.workloads?.[key]?.[stage.id]),
      ).map((stage) => ({
        label: `${settings.flow.workloads[key]} · ${stage.name}`,
        before: routeSummary(settings.flow.configuration.workloads?.[key]?.[stage.id] || settings.flow.configuration.routes[stage.id], settings.flow.configuration),
        after: routeSummary(configuration.workloads?.[key]?.[stage.id] || configuration.routes[stage.id]),
      }))),
    ...Object.entries(configuration.models)
      .filter(
        ([key, profile]) =>
          JSON.stringify(profile) !==
          JSON.stringify(settings.flow.configuration.models[key]),
      )
      .map(([key, profile]) => ({
        label: `Model · ${profile.name}`,
        before: settings.flow.configuration.models[key]
          ? `${settings.flow.configuration.models[key].name} · ${settings.flow.configuration.models[key].model}`
          : "New model",
        after: `${profile.name} · ${profile.model}`,
      })),
    ...Object.entries(settings.flow.configuration.models)
      .filter(([key]) => !(key in configuration.models))
      .map(([, profile]) => ({
        label: `Model · ${profile.name}`,
        before: profile.model,
        after: "Removed",
      })),
    ...settings.flow.stages
      .filter(
        (item) =>
          JSON.stringify(configuration.routes[item.id]) !==
          JSON.stringify(settings.flow.configuration.routes[item.id]),
      )
      .map((item) => ({
        label: item.name,
        before: `${describeRoute(settings.flow.configuration.routes[item.id], settings.flow.configuration)} · timeout ${settings.flow.configuration.routes[item.id].timeout_seconds || "shared"} · tokens ${settings.flow.configuration.routes[item.id].max_output_tokens || "default"}`,
        after: `${describeRoute(configuration.routes[item.id], configuration)} · timeout ${configuration.routes[item.id].timeout_seconds || "shared"} · tokens ${configuration.routes[item.id].max_output_tokens || "default"}`,
      })),
    ...(JSON.stringify(configuration.reviewers) !==
    JSON.stringify(settings.flow.configuration.reviewers)
      ? [
          {
            label: "Reviewers",
            before: settings.flow.configuration.reviewers
              .map(
                (reviewer) =>
                  `${reviewer.name}: ${describeRoute(reviewer, settings.flow.configuration)} · ${reviewer.focus}`,
              )
              .join("\n"),
            after:
              configuration.reviewers
                .map(
                  (reviewer) =>
                    `${reviewer.name}: ${describeRoute(reviewer, configuration)} · ${reviewer.focus}`,
                )
                .join("\n") || "Off",
          },
        ]
      : []),
    ...Object.entries(changes).map(([key, next]) => {
      const item = [...settings.prompts, ...settings.parameters].find(
        (entry) => entry.key === key,
      );
      const field = key.split(":").at(-1) || key;
      return {
        label: item
          ? [
              settings.flow.stages.find((stage) => stage.id === item.stage)
                ?.name || item.group,
              item.section,
              item.label || item.name,
            ]
              .filter(Boolean)
              .join(" · ")
          : key === settings.style.key
            ? "Shared language rule"
            : field,
        before: String(
          item?.prompt ??
            item?.value ??
            (key === settings.style.key
              ? settings.style.value
              : settings.runtime?.[field]) ??
            "",
        ),
        after: String(next ?? item?.default ?? settings.style.default),
      };
    }),
  ];
  return (
    <div className={styles.studio}>
      <header className={styles.heading}>
        <div>
          <h2>LLM</h2>
          <span>{settings.scope}</span>
        </div>
        <div className={styles.actions}>
          {count ? (
            <button
              className="quiet-button"
              type="button"
              disabled={saving}
              onClick={discard}
            >
              Discard
            </button>
          ) : null}
          <button
            className="primary-button"
            type="button"
            disabled={!count || saving}
            onClick={() => (reviewing ? void save() : setReviewing(true))}
          >
            {saving
              ? "Saving…"
              : reviewing
                ? "Save configuration"
                : "Review changes"}
          </button>
        </div>
      </header>
      {error ? (
        <p className="subtle-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice && !count ? <p role="status">{notice}</p> : null}
      <fieldset disabled={saving} className={styles.editor}>
        {reviewing ? (
          <section className={styles.changeReview}>
            <button
              className={styles.textButton}
              type="button"
              onClick={() => setReviewing(false)}
            >
              ← Back to configuration
            </button>
            <h3>Review changes</h3>
            {diffs.map((entry, index) => (
              <div className={styles.diff} key={`${entry.label}-${index}`}>
                <h4>{entry.label}</h4>
                <div>
                  <pre>{entry.before}</pre>
                  <span>→</span>
                  <pre>{entry.after}</pre>
                </div>
              </div>
            ))}
            <p className={styles.secondary}>
              New runs use the saved configuration. Running research keeps its
              current settings.
            </p>
          </section>
        ) : (
          <>
            <nav className={styles.tabs} aria-label="LLM configuration">
              {[
                ["flow", "Research flow"],
                ["models", "Models"],
                ["other", "Other workflows"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={tab === key}
                  onClick={() => setTab(key)}
                >
                  {label}
                </button>
              ))}
              <span>{count ? "Unsaved changes" : "Saved configuration"}</span>
            </nav>
            {tab === "flow" ? (
              <section className={styles.workspace}>
                <div className={styles.workload}>
                  <label>Workload
                    <select aria-label="Research workload" value={workload} onChange={(event) => setWorkload(event.target.value)}>
                      {Object.entries(settings.flow.workloads).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                  </label>
                </div>
                <LLMFlowGraph
                  stages={settings.flow.stages}
                  edges={settings.flow.edges}
                  configuration={{ ...configuration, routes: effectiveRoutes }}
                  selected={stageId}
                  onSelect={selectStage}
                />
                <section
                  className={styles.inspector}
                  aria-label="Selected step"
                >
                  <header>
                    <div>
                      <h3>{stage.name}</h3>
                      <span>{stage.kind}</span>
                    </div>
                    <nav aria-label="Step details">
                      {["settings", "instructions", "data"].map((key) => (
                        <button
                          type="button"
                          key={key}
                          aria-pressed={detail === key}
                          onClick={() => setDetail(key)}
                        >
                          {key[0].toUpperCase() + key.slice(1)}
                        </button>
                      ))}
                    </nav>
                  </header>
                  {detail === "settings" ? (
                    <>
                      {stageId === "review" ? (
                        <div className={styles.reviewers}>
                          {configuration.reviewers.map((reviewer) => (
                            <div className={styles.reviewer} key={reviewer.id}>
                              <header>
                                <input
                                  aria-label="Reviewer name"
                                  value={reviewer.name}
                                  onChange={(event) =>
                                    reviewerChange(reviewer.id, {
                                      name: event.target.value,
                                    })
                                  }
                                />
                                <button
                                  className={styles.textButton}
                                  type="button"
                                  onClick={() =>
                                    setConfiguration((previous) => ({
                                      ...previous,
                                      reviewers: previous.reviewers.filter(
                                        (item) => item.id !== reviewer.id,
                                      ),
                                    }))
                                  }
                                >
                                  Remove
                                </button>
                              </header>
                              {routeFields(
                                reviewer,
                                (update) => reviewerChange(reviewer.id, update),
                                reviewer.name,
                              )}
                              <label>
                                Review focus
                                <textarea
                                  rows={2}
                                  value={reviewer.focus}
                                  onChange={(event) =>
                                    reviewerChange(reviewer.id, {
                                      focus: event.target.value,
                                    })
                                  }
                                />
                              </label>
                              {limits(reviewer, (update) =>
                                reviewerChange(reviewer.id, update),
                              )}
                            </div>
                          ))}
                          <button
                            className={styles.textButton}
                            disabled={configuration.reviewers.length >= 8}
                            type="button"
                            onClick={() =>
                              setConfiguration((previous) => ({
                                ...previous,
                                reviewers: [
                                  ...previous.reviewers,
                                  {
                                    ...previous.routes.review,
                                    id: crypto.randomUUID(),
                                    name: `Reviewer ${previous.reviewers.length + 1}`,
                                    focus:
                                      "Alternative explanations and missing evidence",
                                  },
                                ],
                              }))
                            }
                          >
                            + Add reviewer
                          </button>
                        </div>
                      ) : stage.inference === false ? null : (
                        <>
                          {routeFields(
                            effectiveRoutes[stageId],
                            (update) => routeChange(stageId, update),
                            stage.name,
                          )}
                          {limits(effectiveRoutes[stageId], (update) =>
                            routeChange(stageId, update),
                          )}
                          {configuration.workloads?.[workload]?.[stageId] ? (
                            <button className={styles.textButton} type="button" onClick={() => inheritRoute(stageId)}>Use shared route</button>
                          ) : null}
                        </>
                      )}
                      {stageId === "review" ? (
                        <details className={styles.advanced}>
                          <summary>Other review tasks</summary>
                          {routeFields(
                            effectiveRoutes.review,
                            (update) => routeChange("review", update),
                            "Other review tasks",
                          )}
                          {limits(effectiveRoutes.review, (update) =>
                            routeChange("review", update),
                          )}
                        </details>
                      ) : null}
                      <div className={styles.parameters}>
                        {visibleParameters
                          .filter((item) => !item.section)
                          .map(settingField)}
                      </div>
                      {Array.from(
                        new Set(
                          visibleParameters
                            .map((item) => item.section)
                            .filter(Boolean),
                        ),
                      ).map((section) => (
                        <details className={styles.advanced} key={section}>
                          <summary>{section}</summary>
                          <div className={styles.parameters}>
                            {visibleParameters
                              .filter((item) => item.section === section)
                              .map(settingField)}
                          </div>
                        </details>
                      ))}
                      <div className={styles.auxiliary}>
                        {settings.flow.stages
                          .filter(
                            (item) => item.auxiliary && item.id !== stageId,
                          )
                          .map((item) => (
                            <button
                              type="button"
                              key={item.id}
                              className={styles.textButton}
                              onClick={() => selectStage(item.id)}
                            >
                              {item.name}
                            </button>
                          ))}
                      </div>
                    </>
                  ) : detail === "instructions" ? (
                    <>
                      {prompts.map(settingField)}
                      <details className={styles.advanced}>
                        <summary>Shared language rule</summary>
                        <label>
                          All human-facing output
                          <textarea
                            rows={8}
                            value={String(
                              value(
                                settings.style.key,
                                settings.style.value,
                                settings.style.default,
                              ),
                            )}
                            onChange={(event) =>
                              change(
                                settings.style.key,
                                event.target.value,
                                settings.style.value,
                              )
                            }
                          />
                        </label>
                        <button
                          className={styles.textButton}
                          type="button"
                          onClick={() =>
                            change(
                              settings.style.key,
                              null,
                              settings.style.value,
                            )
                          }
                        >
                          Use default
                        </button>
                      </details>
                    </>
                  ) : (
                    <>
                      <div className={styles.data}>
                        <div>
                          <h4>Receives</h4>
                          <ul>
                            {stage.inputs.map((line) => (
                              <li key={line}>{line}</li>
                            ))}
                          </ul>
                        </div>
                        <div>
                          <h4>Produces</h4>
                          <ul>
                            {stage.outputs.map((line) => (
                              <li key={line}>{line}</li>
                            ))}
                          </ul>
                        </div>
                        <div>
                          <h4>Hands to</h4>
                          <ul>
                            {settings.flow.edges
                              .filter((edge) => edge.source === stageId)
                              .map((edge) => (
                                <li key={edge.target}>
                                  {
                                    settings.flow.stages.find(
                                      (item) => item.id === edge.target,
                                    )?.name
                                  }
                                  : {edge.label}
                                </li>
                              ))}
                          </ul>
                        </div>
                      </div>
                      {usage.length ? (
                        <div className={styles.usage}>
                          <h4>
                            Observed · last {settings.stage_usage?.days || 7}{" "}
                            days
                          </h4>
                          <table>
                            <thead>
                              <tr>
                                <th>Requested model</th>
                                <th>Reported model</th>
                                <th>HTTP attempts</th>
                                <th>Failed</th>
                                <th>Unfinished</th>
                                <th>HTTP seconds</th>
                                <th>Input / output tokens</th>
                                <th>Estimated USD</th>
                              </tr>
                            </thead>
                            <tbody>
                              {usage.map((row, index) => (
                                <tr key={index}>
                                  <td>{row.requested_model}</td>
                                  <td>{row.reported_model || "—"}</td>
                                  <td>{row.attempts}</td>
                                  <td>{row.failures}</td>
                                  <td>{row.unfinished_attempts ?? 0}</td>
                                  <td>{Number(row.seconds).toFixed(1)}</td>
                                  <td>
                                    {row.input_tokens === null
                                      ? "—"
                                      : Number(
                                          row.input_tokens,
                                        ).toLocaleString()}{" "}
                                    /{" "}
                                    {row.output_tokens === null
                                      ? "—"
                                      : Number(
                                          row.output_tokens,
                                        ).toLocaleString()}
                                  </td>
                                  <td>
                                    {row.estimated_usd_min === null
                                      ? "—"
                                      : `${Number(row.estimated_usd_min).toFixed(4)}–${Number(row.estimated_usd_max).toFixed(4)}`}
                                    {row.unpriced_attempts ? (
                                      <small>
                                        {row.unpriced_attempts} unpriced
                                      </small>
                                    ) : null}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : null}
                    </>
                  )}
                </section>
              </section>
            ) : tab === "models" ? (
              <section className={styles.models}>
                {Object.entries(configuration.models).map(([key, model]) => (
                  <div className={styles.fields} key={key}>
                    <label>
                      Profile name
                      <input
                        value={model.name}
                        onChange={(event) =>
                          setConfiguration((previous) => ({
                            ...previous,
                            models: {
                              ...previous.models,
                              [key]: { ...model, name: event.target.value },
                            },
                          }))
                        }
                      />
                    </label>
                    <label>
                      {settings.runtime?.provider === "azure_openai"
                        ? "Deployment"
                        : "Model ID"}
                      <input
                        value={model.model}
                        onChange={(event) =>
                          setConfiguration((previous) => ({
                            ...previous,
                            models: {
                              ...previous.models,
                              [key]: { ...model, model: event.target.value },
                            },
                          }))
                        }
                      />
                    </label>
                    <button
                      type="button"
                      className={styles.textButton}
                      aria-label={`Remove ${model.name}`}
                      disabled={
                        usedProfiles.has(key) ||
                        Object.keys(configuration.models).length === 1
                      }
                      onClick={() =>
                        setConfiguration((previous) => {
                          const models = { ...previous.models };
                          delete models[key];
                          return { ...previous, models };
                        })
                      }
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  className={styles.textButton}
                  disabled={Object.keys(configuration.models).length >= 12}
                  type="button"
                  onClick={() => {
                    const id = crypto.randomUUID();
                    setConfiguration((previous) => ({
                      ...previous,
                      models: {
                        ...previous.models,
                        [id]: { name: "New model", model: "" },
                      },
                    }));
                  }}
                >
                  + Add model
                </button>
                <details className={styles.advanced}>
                  <summary>
                    Connection defaults · {settings.runtime?.provider}
                  </summary>
                  <div className={styles.fields}>
                    {["temperature", "timeout_seconds"].map((field) => (
                      <label key={field}>
                        {field === "temperature"
                          ? "Temperature"
                          : "Request timeout (seconds)"}
                        <input
                          type="number"
                          step={field === "temperature" ? 0.1 : 1}
                          min={field === "temperature" ? 0 : 10}
                          max={field === "temperature" ? 2 : 1800}
                          value={String(
                            value(
                              `runtime:${settings.runtime?.provider}:${field}`,
                              settings.runtime?.[field],
                              settings.runtime?.[field],
                            ) ?? "",
                          )}
                          onChange={(event) =>
                            change(
                              `runtime:${settings.runtime?.provider}:${field}`,
                              Number(event.target.value),
                              settings.runtime?.[field],
                            )
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <p className={styles.secondary}>
                    Embeddings:{" "}
                    {String(
                      settings.runtime?.embedding_model || "Unconfigured",
                    )}
                  </p>
                </details>
              </section>
            ) : (
              <div className={styles.other}>
                {otherGroups.map((group) => (
                  <details key={group}>
                    <summary>{group}</summary>
                    {others
                      .filter((item) => (item.group || "Other") === group)
                      .map(settingField)}
                  </details>
                ))}
              </div>
            )}
          </>
        )}
      </fieldset>
    </div>
  );
}
