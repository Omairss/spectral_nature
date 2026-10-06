"use client";

import { useCallback, useEffect, useState } from "react";
import type { PortfolioView } from "@/lib/types";
import { responseError } from "./api";

export function usePortfolio(period: string, enabled: boolean) {
  const [portfolio, setPortfolio] = useState<PortfolioView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!enabled) return;
    await Promise.resolve();
    setLoading(true); setError("");
    try {
      const response = await fetch(`/api/portfolio?period=${encodeURIComponent(period)}`, { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error(await responseError(response, "Portfolio data could not be loaded."));
      setPortfolio((await response.json()) as PortfolioView);
    } catch (reason) {
      setPortfolio(null);
      setError(reason instanceof Error ? reason.message : "Portfolio data could not be loaded.");
    } finally { setLoading(false); }
  }, [enabled, period]);
  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  return { portfolio, loading, error, reload: load };
}
