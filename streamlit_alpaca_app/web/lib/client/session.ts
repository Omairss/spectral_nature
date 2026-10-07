"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Session } from "@/lib/types";

let sessionRequest: Promise<Session> | null = null;

export function readAuthenticatedSession(): Promise<Session> {
  // The shell and route mount together. One request owns cookie refresh; neither
  // may interpret an unavailable authentication service as a signed-out user.
  if (!sessionRequest) {
    sessionRequest = fetch("/api/auth/session", { cache: "no-store", credentials: "same-origin" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Session could not be verified.");
        const payload = await response.json() as Session;
        if (typeof payload.authenticated !== "boolean") throw new Error("Session could not be verified.");
        return payload;
      })
      .finally(() => { sessionRequest = null; });
  }
  return sessionRequest;
}

export function useAuthenticatedSession(returnPath: string) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [sessionError, setSessionError] = useState("");

  useEffect(() => {
    let active = true;
    readAuthenticatedSession()
      .then((payload: Session) => {
        if (!active) return;
        setSessionError("");
        setSession(payload);
        if (!payload.authenticated) router.replace(`/login?next=${encodeURIComponent(returnPath)}`);
      })
      .catch(() => { if (active) setSessionError("Session could not be verified."); });
    return () => { active = false; };
  }, [returnPath, router]);

  return { session, sessionError };
}
