"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Session } from "@/lib/types";
import { Icon, SignalMark, type IconName } from "./icons";

const navigation: Array<{ href: string; label: string; icon: IconName }> = [
  { href: "/", label: "Daily Brief", icon: "home" },
  { href: "/zopedia", label: "Zopedia", icon: "book" },
  { href: "/broad-economy", label: "Broad Economy", icon: "globe" },
  { href: "/market-explorer", label: "Market Explorer", icon: "compass" },
  { href: "/portfolio", label: "Portfolio", icon: "database" },
  { href: "/portfolio-performance", label: "Performance", icon: "chart" },
  { href: "/stock-investigator", label: "Investigator", icon: "chart" },
  { href: "/options", label: "Options", icon: "table" },
  { href: "/trading-agent", label: "Trading Agent", icon: "message" },
  { href: "/admin", label: "Admin", icon: "shield" },
];

export function ShellNav({ session, onLogout }: { session: Session | null; onLogout: () => void }) {
  const pathname = usePathname();
  return (
    <aside className="product-rail">
      <Link className="brand" href="/" aria-label="Spectral Nature home">
        <SignalMark />
        <span className="wordmark">
          <strong>Spectral Nature</strong>
        </span>
      </Link>

      <nav className="primary-nav" aria-label="Primary">
        <span className="nav-section-label">Workspace</span>
        {navigation.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              aria-current={active ? "page" : undefined}
              aria-label={item.label}
              className={active ? "nav-link active" : "nav-link"}
              href={item.href}
              key={item.href}
            >
              <Icon name={item.icon} />
              <span className="nav-label">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="rail-account">
        <span className="torres-signoff">by Torres Capital</span>
        {session?.authenticated ? (
          <div className="account-row">
            <span className="account-avatar">{(session.context?.display_name || session.context?.email || "S").charAt(0).toUpperCase()}</span>
            <span className="account-copy">
              <strong>{session.context?.display_name || session.context?.email}</strong>
              <small>{session.context?.role || "Member"}</small>
            </span>
            <button aria-label="Sign out" className="icon-button" onClick={onLogout} type="button"><Icon name="logout" /></button>
          </div>
        ) : session ? (
          <Link className="signin-link" href="/login">Sign in</Link>
        ) : null}
      </div>
    </aside>
  );
}
