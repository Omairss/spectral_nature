import "server-only";
import { notFound } from "next/navigation";
import { loadHomePublication } from "@/lib/server/home";

export async function loadAttentionEvent(version: string, event: string) {
  try { event = decodeURIComponent(event); } catch { notFound(); }
  if (!/^attention_home_snapshots_1d__[A-Za-z0-9_]+$/.test(version) || event.length > 160) notFound();
  const { home } = await loadHomePublication("", version);
  if (!home || home.status !== "available" || home.source_version !== version) notFound();
  const beat = home.beats.find(item => item.id === event);
  if (!beat) notFound();
  return { home, beat };
}
