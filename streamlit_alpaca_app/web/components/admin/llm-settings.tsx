"use client";

import { useState } from "react";
import { responseError, secureMutation } from "@/lib/client/api";

type Setting = Record<string, unknown>;
export type LLMSettings = {
  scope: string;
  runtime: (Record<string, unknown> & { provider: string; model: string; model_editable: boolean }) | null;
  style: { key: string; value: string; default: string; is_override: boolean };
  prompts: Setting[];
  parameters: Setting[];
};
type Change = string | number | null;

export function LLMSettingsEditor({ settings, onSaved }: { settings: LLMSettings; onSaved: (settings: LLMSettings) => void }) {
  const [changes, setChanges] = useState<Record<string, Change>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const count = Object.keys(changes).length;
  function change(key: string, value: Change, active: unknown) {
    setNotice("");
    setChanges((previous) => {
      const next = { ...previous };
      if (value === active) delete next[key];
      else next[key] = value;
      return next;
    });
  }
  function value(key: string, active: unknown, fallback: unknown) {
    return key in changes ? changes[key] ?? fallback : active;
  }
  async function save() {
    setSaving(true); setError(""); setNotice("");
    try {
      const response = await secureMutation("/api/admin/llm", { method: "PATCH", body: JSON.stringify({ changes }) });
      if (!response.ok) throw new Error(await responseError(response, "LLM settings could not be saved."));
      onSaved(await response.json() as LLMSettings);
      setChanges({}); setNotice("Saved. New research uses these settings within 30 seconds.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "LLM settings could not be saved."); }
    finally { setSaving(false); }
  }
  const runtime = settings.runtime;
  const style = settings.style;
  return <div className="llm-settings">
    <div className="inline-controls"><span>{settings.scope}</span><button className="primary-button" disabled={saving || !count} onClick={() => void save()} type="button">{saving ? "Saving…" : "Save changes"}</button>{count ? <button className="quiet-button" disabled={saving} onClick={() => setChanges({})} type="button">Discard changes</button> : null}</div>
    {error ? <p className="subtle-error" role="alert">{error}</p> : null}
    {notice ? <p className="admin-notice" role="status">{notice}</p> : null}
    <fieldset disabled={saving} className="llm-settings-fields">
      {runtime ? <section className="workbook-panel admin-form"><header className="panel-header"><h2>Model</h2><span>{runtime.provider}</span></header>
        <div className="admin-form-grid">
          {([['model', 'Model', 'text'], ['temperature', 'Temperature', 'number'], ['timeout_seconds', 'Request timeout (seconds)', 'number']] as const).map(([field, label, type]) => {
            const key = `runtime:${runtime.provider}:${field}`;
            return <label key={key}>{label}<input disabled={field === 'model' && !runtime.model_editable} type={type} step={field === 'temperature' ? '0.1' : '1'} min={field === 'temperature' ? '0' : field === 'timeout_seconds' ? '10' : undefined} max={field === 'temperature' ? '2' : field === 'timeout_seconds' ? '1800' : undefined} value={String(value(key, runtime[field], runtime[field]) ?? '')} onChange={(event) => change(key, type === 'number' && event.target.value ? Number(event.target.value) : event.target.value, runtime[field])} /></label>;
          })}
          <label>Reasoning effort<select value={String(value(`runtime:${runtime.provider}:reasoning_effort`, runtime.reasoning_effort, '') || '')} onChange={(event) => change(`runtime:${runtime.provider}:reasoning_effort`, event.target.value, runtime.reasoning_effort)}>{['', 'minimal', 'low', 'medium', 'high', 'max', 'xhigh', 'ultra'].map((effort) => <option value={effort} key={effort}>{effort || 'Provider default'}</option>)}</select></label>
          {runtime.provider === 'deepseek' ? <label>Thinking mode<select value={String(value('runtime:deepseek:thinking_mode', runtime.thinking_mode, 'enabled') || 'enabled')} onChange={(event) => change('runtime:deepseek:thinking_mode', event.target.value, runtime.thinking_mode)}><option value="enabled">Enabled</option><option value="disabled">Disabled</option></select></label> : null}
        </div>
        <p className="prompt-file">Provider, credentials, endpoint, and Azure deployment are set by deployment configuration.</p>
        <button className="quiet-button" onClick={() => setChanges((previous) => ({ ...previous, ...Object.fromEntries(['model', 'temperature', 'timeout_seconds', 'reasoning_effort', ...(runtime.provider === 'deepseek' ? ['thinking_mode'] : [])].filter((field) => field !== 'model' || runtime.model_editable).map((field) => [`runtime:${runtime.provider}:${field}`, null])) }))} type="button">Use deployment defaults</button>
      </section> : null}
      <section className="workbook-panel admin-form"><header className="panel-header"><h2>Language rule</h2><span>{style.is_override ? 'Custom' : 'Default'}</span></header><label>All human-facing LLM output<textarea rows={8} value={String(value(style.key, style.value, style.default))} onChange={(event) => change(style.key, event.target.value, style.value)} /></label><button className="quiet-button" onClick={() => change(style.key, null, style.value)} type="button">Use default language rule</button></section>
      <section className="prompt-list"><header className="panel-header"><h2>Prompts</h2><span>{settings.prompts.length}</span></header>{settings.prompts.map((prompt) => {
        const key = String(prompt.key);
        return <details className="workbook-panel" key={key}><summary><span>{String(prompt.name)}</span><small>{String(prompt.group || '')}{prompt.is_override ? ' · Custom' : ''}</small></summary><div className="admin-form"><label>{String(prompt.name)}<textarea rows={12} value={String(value(key, prompt.prompt, prompt.default))} onChange={(event) => change(key, event.target.value, prompt.prompt)} /></label><button className="quiet-button" onClick={() => change(key, null, prompt.prompt)} type="button">Use default prompt</button></div></details>;
      })}</section>
      <details className="workbook-panel llm-tuning"><summary>Tuning parameters</summary><div className="admin-form-grid admin-form">{settings.parameters.map((parameter) => {
        const key = String(parameter.key);
        return <div className="llm-parameter" key={key}><label>{String(parameter.name)}{Array.isArray(parameter.choices) && parameter.choices.length ? <select value={String(value(key, parameter.value, parameter.default))} onChange={(event) => change(key, event.target.value, parameter.value)}>{parameter.choices.map((choice) => <option value={String(choice)} key={String(choice)}>{String(choice)}</option>)}</select> : <input type={typeof parameter.default === 'string' ? 'text' : 'number'} min={typeof parameter.default === 'string' ? undefined : '0'} step={Number.isInteger(Number(parameter.default)) ? '1' : 'any'} value={String(value(key, parameter.value, parameter.default))} onChange={(event) => change(key, typeof parameter.default === 'string' || !event.target.value ? event.target.value : Number(event.target.value), parameter.value)} />}</label><small>{String(parameter.description)}</small><button className="quiet-button" onClick={() => change(key, null, parameter.value)} type="button">Use default ({String(parameter.default)})</button></div>;
      })}</div></details>
    </fieldset>
  </div>;
}
