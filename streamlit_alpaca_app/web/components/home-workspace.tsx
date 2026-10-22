"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/markdown";
import { Visualization } from "@/components/visualizations/visualization";
import { AttentionTickerCard } from "@/components/attention-ticker-card";
import { TrendChart } from "@/components/visualizations/trend-chart";
import type { HomeView } from "@/lib/types";

function todayUtc() {
  return new Date().toISOString().slice(0, 10);
}

function formatAsOf(value: string) {
  const parsed = new Date(value);
  if (!value || Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(parsed);
}

function beatKind(kind: string) {
  if (kind === "event") return "Event";
  if (kind === "unresolved") return "Watch";
  return "Mover";
}

export function HomeWorkspace({ initialHome, initialEtag, targetDate }: {
  initialHome: HomeView | null; initialEtag: string; targetDate: string;
}) {
  const [home, setHome] = useState(initialHome);
  const audio = useRef<HTMLAudioElement>(null);
  const applyPending = useRef<() => void>(() => {});
  useEffect(() => {
    if (targetDate) return;
    let etag = initialEtag;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    let pending = initialHome?.status === "available" ? initialHome : null;
    let disposed = false;
    const apply = () => {
      const media = audio.current;
      if (pending && (!media || media.paused || media.ended)) {
        setHome(pending);
        pending = null;
      }
    };
    applyPending.current = apply;
    const check = async () => {
      if (disposed || document.visibilityState !== "visible" || request) return;
      clearTimeout(timer);
      const controller = new AbortController();
      request = controller;
      const timeout = setTimeout(() => controller.abort(), 20_000);
      try {
        const response = await fetch("/api/home", {
          cache: "no-store", signal: controller.signal,
          headers: etag ? { "If-None-Match": etag } : {},
        });
        if (response.status === 304 && !controller.signal.aborted) apply();
        else if (response.ok) {
          const next = await response.json() as HomeView;
          if (!controller.signal.aborted && next.status === "available" && !next.is_replay) {
            const nextEtag = response.headers.get("ETag") || "";
            if (!nextEtag || nextEtag !== etag) {
              etag = nextEtag;
              pending = next;
              apply();
            }
          }
        }
      } catch {
        // Keep the displayed publication during a failed background read.
      } finally {
        clearTimeout(timeout);
        if (request === controller) request = undefined;
        if (!disposed && document.visibilityState === "visible") timer = setTimeout(check, 30_000);
      }
    };
    const resume = () => {
      clearTimeout(timer);
      if (document.visibilityState === "visible") void check();
      else request?.abort();
    };
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("online", resume);
    void check();
    return () => {
      disposed = true;
      clearTimeout(timer);
      request?.abort();
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("focus", resume);
      window.removeEventListener("online", resume);
      applyPending.current = () => {};
    };
  }, [initialHome, initialEtag, targetDate]);
  const available = home?.status === "available";
  return (
    <div className="page-shell home-workspace">
      <header className="artifact-header">
        <div>
          <p className="breadcrumb">Home <span>/</span> Daily Brief</p>
          <h1>Market signal</h1>
        </div>
        <div className="home-header-tools">
          {available && home.as_of ? <time dateTime={home.as_of}>{formatAsOf(home.as_of)}</time> : null}
          <form action="/" className="replay-control" method="get">
            <label htmlFor="home-replay-date">Replay</label>
            <input defaultValue={targetDate} id="home-replay-date" max={todayUtc()} name="date" type="date" />
            <button type="submit">View</button>
          </form>
        </div>
      </header>

      {available && home.is_replay ? (
        <div className="replay-banner">
          <span>Historical snapshot <strong>{home.view_date}</strong></span>
          <Link href="/">Return to latest</Link>
        </div>
      ) : null}

      {!available ? (
        <section className="empty-state" aria-live="polite">
          <strong>Daily Brief unavailable</strong>
          <p>The latest materialized market view could not be loaded.</p>
        </section>
      ) : (
        <>
          {home.summary ? (
            <section className="brief-panel">
              <div className="brief-copy">
                <p className="section-label">Daily brief</p>
                <h2>{home.summary.headline}</h2>
                <Markdown>{home.summary.markdown}</Markdown>
                {home.summary.audio_available ? (
                  <audio
                    ref={audio}
                    key={`${home.run_id}:${home.as_of}:${home.summary.markdown}`}
                    onPause={() => applyPending.current()}
                    onEnded={() => applyPending.current()}
                    aria-label="Daily Brief audio"
                    className="brief-audio"
                    controls
                    preload="none"
                    src={`/api/home/audio${home.is_replay && home.view_date ? `?date=${encodeURIComponent(home.view_date)}` : ""}`}
                  />
                ) : null}
              </div>
              {home.daily_datapoints?.length ? <aside className="daily-datapoints" aria-label="Five crucial datapoints"><p className="section-label">Highlights{home.daily_datapoints_as_of ? <time dateTime={home.daily_datapoints_as_of}> · {new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(home.daily_datapoints_as_of))}</time> : null}</p>{home.daily_datapoints.map((point) => <article key={point.id}><h3>{point.title}</h3><TrendChart label={point.label} unit={point.unit} points={point.points} price={point.kind === "price"} tone={point.kind === "macro" ? "violet" : "teal"} /><p>{point.significance}</p><time dateTime={point.date}>{point.period ? `${point.period} · ` : ""}{point.date}</time></article>)}</aside> : null}
            </section>
          ) : null}

          <section className="signal-section" aria-label="Market movements">
            <header className="section-heading">
              <div><p className="section-label">Attention</p><h2>Market movements</h2></div>
              <span>{home.beats.length} signals</span>
            </header>
            <ol className="signal-list">
              {home.beats.map((beat, index) => (
                <li className={`signal-row signal-${beat.kind}`} key={beat.id || `${beat.kind}-${index}`}>
                  <span className="signal-index">{String(index + 1).padStart(2, "0")}</span>
                  <article>
                    <div className="signal-meta">
                      <span className="signal-kind">{beatKind(beat.kind)}</span>
                      {beat.symbols.length ? <span>{beat.symbols.length} {beat.symbols.length === 1 ? "company" : "companies"}</span> : null}
                    </div>
                    <h3>{beat.title}</h3>
                    {beat.summary ? <p>{beat.summary}</p> : null}
                    {beat.why ? <p>{beat.why}</p> : null}
                    {beat.business_context ? <p className="business-context">{beat.business_context}</p> : null}
                    {beat.watch_next ? <p><strong>Watch next: </strong>{beat.watch_next}</p> : null}
                    {beat.tickers.length ? <div className="attention-ticker-grid">{beat.tickers.map((ticker) => <AttentionTickerCard key={ticker.symbol} ticker={ticker} />)}</div> : null}
                  </article>
                </li>
              ))}
            </ol>
          </section>

          {home.graph ? (
            <section className="home-graph-panel" aria-label="Market attention network">
              <header className="section-heading">
                <div><p className="section-label">Structure</p><h2>{home.graph.title}</h2></div>
                <span>{home.graph.performance.row_count.toLocaleString()} plotted points</span>
              </header>
              <Visualization artifact={home.graph} />
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
