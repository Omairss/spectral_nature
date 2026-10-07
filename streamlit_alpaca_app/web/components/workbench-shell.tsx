"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { Session } from "@/lib/types";
import { secureMutation } from "@/lib/client/api";
import { readAuthenticatedSession } from "@/lib/client/session";
import { AgentRail } from "./agent-rail";
import { Icon, SignalMark } from "./icons";
import { ShellNav } from "./shell-nav";

function tabForPath(pathname: string) {
  if (pathname.startsWith("/broad-economy")) return { label: "Broad Economy", icon: "globe" as const };
  if (pathname.startsWith("/market-explorer")) return { label: "Market Explorer", icon: "compass" as const };
  if (pathname.startsWith("/portfolio-performance")) return { label: "Portfolio Performance", icon: "chart" as const };
  if (pathname.startsWith("/portfolio")) return { label: "Portfolio", icon: "database" as const };
  if (pathname.startsWith("/options")) return { label: "Option Strategizer", icon: "table" as const };
  if (pathname.startsWith("/trading-agent")) return { label: "Trading Agent", icon: "message" as const };
  if (pathname.startsWith("/admin")) return { label: "Admin", icon: "shield" as const };
  if (pathname.startsWith("/stock-investigator")) return { label: "Stock Investigator", icon: "chart" as const };
  if (pathname.startsWith("/zopedia/runs/")) return { label: "Research", icon: "book" as const };
  if (pathname.startsWith("/zopedia")) return { label: "Zopedia", icon: "book" as const };
  return { label: "Daily Brief", icon: "home" as const };
}

export function WorkbenchShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const tab = useMemo(() => tabForPath(pathname), [pathname]);
  const conversationOwnsCanvas = pathname === "/zopedia";

  useEffect(() => {
    let active = true;
    readAuthenticatedSession()
      .then((payload: Session) => { if (active) setSession(payload); })
      .catch(() => { /* Keep the last verified identity during an auth-store outage. */ });
    return () => { active = false; };
  }, [pathname]);

  async function logout() {
    await secureMutation("/api/auth/logout", { method: "POST" });
    setSession({ authenticated: false });
    router.push("/");
    router.refresh();
  }

  if (pathname.startsWith("/login")) {
    return (
      <div className="auth-workspace">
        <header className="auth-brand">
          <SignalMark compact />
          <span><strong>Spectral Nature</strong><small>by Torres Capital</small></span>
        </header>
        <main>{children}</main>
      </div>
    );
  }

  return (
    <div className={conversationOwnsCanvas ? "app-frame conversation-canvas" : "app-frame"}>
      <ShellNav onLogout={logout} session={session} />
      <main className="main-canvas">
        <header className="workspace-tabs">
          <div className="mobile-brand"><SignalMark compact /><strong>Spectral Nature</strong></div>
          <div className="active-tab"><Icon name={tab.icon} /><span>{tab.label}</span></div>
        </header>
        <div className="canvas-scroll">{children}</div>
      </main>
      {conversationOwnsCanvas ? null : <AgentRail session={session} />}
    </div>
  );
}
