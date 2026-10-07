"use client";

import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { responseError, secureMutation } from "@/lib/client/api";

const subscribeToClient = () => () => {};

export function LoginForm({ destination, action = "login", token = "", invalidLink = false, notice = "" }: {
  destination: string; action?: "login" | "invite" | "reset"; token?: string; invalidLink?: boolean; notice?: string;
}) {
  const router = useRouter();
  const [error, setError] = useState("");
  const interactive = useSyncExternalStore(subscribeToClient, () => true, () => false);
  const [submitting, setSubmitting] = useState(false);
  const [recover, setRecover] = useState(false);
  const [message, setMessage] = useState("");
  const mode = recover ? "recover" : action;
  const title = mode === "invite" ? "Create account" : mode === "reset" ? "Set a new password" : mode === "recover" ? "Reset password" : "Sign in";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setSubmitting(true);
    const form = new FormData(event.currentTarget);
    if ((mode === "invite" || mode === "reset") && form.get("password") !== form.get("confirm_password")) {
      setError("Passwords do not match.");
      setSubmitting(false);
      return;
    }
    try {
      const body = mode === "invite" ? {
        invite_token: token, first_name: form.get("first_name"), last_name: form.get("last_name"),
        display_name: form.get("display_name"), password: form.get("password"),
      } : mode === "reset" ? { reset_token: token, new_password: form.get("password") }
        : mode === "recover" ? { email: form.get("email") }
          : { email: form.get("email"), password: form.get("password") };
      const response = await secureMutation(mode === "login" ? "/api/auth/login" : `/api/auth/account/${mode}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        setError(await responseError(response, "Account request failed."));
        return;
      }
      if (mode === "recover") {
        setMessage("Reset instructions requested.");
        return;
      }
      if (mode === "reset") {
        router.replace("/login?notice=reset");
        router.refresh();
        return;
      }
      router.replace(destination);
      router.refresh();
    } catch {
      setError("Account service is unavailable.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="narrow-page">
      <section className="auth-panel">
        <p className="eyebrow">Account</p>
        <h1>{title}</h1>
        {invalidLink ? <p className="form-error" role="alert">Account link is invalid.</p> : null}
        {notice && mode === "login" ? <p role="status">{notice}</p> : null}
        <form onSubmit={submit}>
          {mode === "login" || mode === "recover" ? <label>Email<input autoComplete="email" maxLength={320} name="email" required type="email" /></label> : null}
          {mode === "invite" ? <>
            <label>First name<input autoComplete="given-name" maxLength={100} name="first_name" required /></label>
            <label>Last name<input autoComplete="family-name" maxLength={100} name="last_name" required /></label>
            <label>Display name<input autoComplete="nickname" maxLength={200} name="display_name" /></label>
          </> : null}
          {mode !== "recover" ? <label>{mode === "reset" ? "New password" : "Password"}<input autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "login" ? undefined : 12} maxLength={mode === "login" ? undefined : 1024} name="password" required type="password" /></label> : null}
          {mode === "invite" || mode === "reset" ? <label>Confirm password<input autoComplete="new-password" minLength={12} maxLength={1024} name="confirm_password" required type="password" /></label> : null}
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          {message ? <p role="status">{message}</p> : null}
          <button className="primary-button" disabled={!interactive || submitting} type="submit">
            {submitting ? "Working…" : mode === "invite" ? "Create account" : mode === "reset" ? "Update password" : mode === "recover" ? "Send reset instructions" : "Sign in"}
          </button>
          {mode === "login" ? <button className="quiet-button" disabled={submitting} onClick={() => { setRecover(true); setError(""); setMessage(""); }} type="button">Forgot password?</button> : null}
          {mode === "recover" ? <button className="quiet-button" disabled={submitting} onClick={() => { setRecover(false); setError(""); setMessage(""); }} type="button">Back to sign in</button> : null}
        </form>
      </section>
    </div>
  );
}
