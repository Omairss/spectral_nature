"use client";

import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { responseError, secureMutation } from "@/lib/client/api";

const subscribeToClient = () => () => {};

export function LoginForm({ destination }: { destination: string }) {
  const router = useRouter();
  const [error, setError] = useState("");
  const interactive = useSyncExternalStore(subscribeToClient, () => true, () => false);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    const form = new FormData(event.currentTarget);
    try {
      const response = await secureMutation("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      if (!response.ok) {
        setError(await responseError(response, "Sign in failed."));
        return;
      }
      router.replace(destination);
      router.refresh();
    } catch {
      setError("Sign in is unavailable.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="narrow-page">
      <section className="auth-panel">
        <p className="eyebrow">Account</p>
        <h1>Sign in</h1>
        <form onSubmit={submit}>
          <label>Email<input autoComplete="email" name="email" required type="email" /></label>
          <label>Password<input autoComplete="current-password" name="password" required type="password" /></label>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <button className="primary-button" disabled={!interactive || submitting} type="submit">
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </div>
  );
}
