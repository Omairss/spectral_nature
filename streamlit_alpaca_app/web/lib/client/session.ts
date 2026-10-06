"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Session } from "@/lib/types";

export function useAuthenticatedSession(returnPath: string) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [sessionError, setSessionError] = useState("");

  useEffect(() => {
    let active = true;
    fetch("/api/auth/session", { cache: "no-store", credentials: "same-origin" })
      .then(async (response) => response.json())
      .then((payload: Session) => {
        if (!active) return;
        setSession(payload);
        if (!payload.authenticated) router.replace(`/login?next=${encodeURIComponent(returnPath)}`);
      })
      .catch(() => { if (active) setSessionError("Session could not be verified."); });
    return () => { active = false; };
  }, [returnPath, router]);

  return { session, sessionError };
}
