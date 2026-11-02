"use client";

import { useEffect, useRef, useState } from "react";
import type { MarketEventCalendar as Calendar } from "@/lib/types";
import styles from "./market-event-calendar.module.css";

export function MarketEventCalendar({ calendar }: { calendar: Calendar }) {
  const viewport = useRef<HTMLDivElement>(null);
  const [focused, setFocused] = useState(calendar.reference_date);
  const focus = useRef(calendar.reference_date);
  const [offset, setOffset] = useState(calendar.days.findIndex(day => day.id === calendar.reference_date));
  const [expanded, setExpanded] = useState(false);
  const [selection, setSelection] = useState<string | null>(null);
  const day = calendar.days.find(item => item.id === focused);
  const event = day?.events.find(item => item.id === selection);
  const dates = calendar.days.map(item => item.id).join("|");
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const align = () => {
      const index = calendar.days.findIndex(item => item.id === focus.current);
      node.scrollLeft = Math.max(0, index) * node.clientWidth / 5;
    };
    align();
    const observer = new ResizeObserver(align);
    observer.observe(node);
    return () => observer.disconnect();
    // Keep focus through publication refreshes; a viewport resize only realigns it.
  }, [calendar.reference_date, dates]);

  function center(index: number) {
    viewport.current?.scrollTo({ left: index * viewport.current.clientWidth / 5,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    setFocused(calendar.days[index].id);
    focus.current = calendar.days[index].id;
  }

  return <section className={styles.calendar} aria-label="Market event calendar">
    <div className={styles.viewport} ref={viewport} onScroll={e => {
      const node = e.currentTarget;
      const position = node.scrollLeft / (node.clientWidth / 5);
      setOffset(position);
      const next = calendar.days[Math.round(position)];
      if (next) { setFocused(next.id); focus.current = next.id; }
    }}>
      {calendar.days.map((item, index) => {
        const important = new Set([...item.events].sort((a, b) => b.importance - a.importance).slice(0, 2).map(e => e.id));
        const depth = Math.abs(index - offset);
        return <div key={item.id} className={styles.day}>
          <div style={{ transform: `scale(${Math.max(.5, 1 - depth * .18)})`, opacity: Math.max(.08, 1 - depth * .38) }}>
            <button className={styles.date} aria-label={item.label} aria-expanded={expanded && item.id === focused}
              onClick={() => { center(index); setExpanded(true); setSelection(null); }}>
              {new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(item.id + "T12:00:00Z"))}
            </button>
            <div className={`${styles.events} ${expanded ? styles.expanded : ""}`}
              style={expanded ? { height: Math.min(7, Math.max(...calendar.days.map(d => d.events.length))) * 40 } : undefined}>
              {(expanded ? item.events : item.events.filter(e => important.has(e.id))).map(entry => <button
                key={entry.id} className={selection === entry.id ? styles.selected : ""}
                aria-label={entry.name} aria-expanded={selection === entry.id}
                style={{ fontSize: `${12 + 2 * entry.importance}px` }}
                onClick={() => { center(index); setExpanded(true); setSelection(expanded && selection !== entry.id ? entry.id : null); }}>
                {entry.ticker || entry.short_label || entry.name}
              </button>)}
            </div>
          </div>
        </div>;
      })}
    </div>
    {expanded && <div className={styles.footer}>
      <span>{day?.label} · {day?.events.length || 0} events</span>
      <button onClick={() => { setExpanded(false); setSelection(null); }} aria-label="Collapse calendar">⌃</button>
    </div>}
    {event && <article className={styles.detail}>
      <header><strong>{event.name}</strong><span>{event.time ? event.time + " ET" : event.timing_label}</span></header>
      {event.confirmation_status === "expected" && <p>Expected date</p>}
      {event.facts?.map(([label, value], index) => <div key={index} className={styles.fact}><span>{label}</span><span>{value}</span></div>)}
      <a href={event.source_url} target="_blank" rel="noreferrer">Source ↗</a>
    </article>}
  </section>;
}
