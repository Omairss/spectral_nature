import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);

function loadModule(path, overrides = {}, globals = {}) {
  const filename = fileURLToPath(new URL(path, import.meta.url));
  const code = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = { exports: {} };
  vm.runInNewContext(code, {
    module: loaded, exports: loaded.exports,
    require: (name) => Object.hasOwn(overrides, name) ? overrides[name] : require(name),
    process: { env: {} }, Buffer, URL, Headers, Response, AbortSignal, ...globals,
  }, { filename });
  return loaded.exports;
}

test("post-login return paths remain on the application origin after URL normalization", () => {
  const auth = loadModule("../lib/server/auth.ts", {
    "server-only": {}, "next/headers": {}, "next/server": {},
  });
  for (const input of ["//evil.example", "/\\evil.example", "/\n/evil.example", "https://evil.example", "javascript:alert(1)", null, "/" + "x".repeat(2048)]) {
    assert.equal(auth.safeReturnPath(input), "/zopedia");
  }
  assert.equal(auth.safeReturnPath("/stock-investigator?ticker=AAPL#income"), "/stock-investigator?ticker=AAPL#income");
  assert.equal(auth.safeReturnPath("/admin"), "/admin");
});

test("production aliases retain same-host origin and CSRF enforcement", async () => {
  const token = "test-csrf-token-which-is-long-enough";
  const auth = loadModule("../lib/server/auth.ts", {
    "server-only": {}, "next/headers": { cookies: async () => ({ get: () => ({ value: token }) }) },
    "next/server": { NextResponse: { json: Response.json } },
  }, { process: { env: { APP_PUBLIC_ORIGINS: "https://primary.example,https://alias.example" } } });
  const request = (host, origin, csrf = token) => ({
    nextUrl: new URL(`https://${host}/api/example`),
    headers: new Headers({ host, origin, "x-csrf-token": csrf }),
  });
  assert.equal(await auth.validateMutation(request("alias.example", "https://alias.example")), null);
  assert.equal(await auth.validateMutation(request("primary.example", "https://primary.example")), null);
  assert.equal((await auth.validateMutation(request("primary.example", "https://alias.example"))).status, 403);
  assert.equal((await auth.validateMutation(request("primary.example", "https://evil.example"))).status, 403);
  assert.equal((await auth.validateMutation(request("primary.example", "https://primary.example", "wrong"))).status, 403);
  assert.equal((await auth.validateMutation(request("primary.example", "null"))).status, 403);
});

test("auth service failure is not a signed-out session and does not destroy cookies", async () => {
  for (const status of [429, 500, 502, 503]) {
    let cleared = 0;
    let refreshed = 0;
    const backend = loadModule("../lib/server/backend.ts", {
      "server-only": {}, "next/headers": { cookies: async () => ({ get: () => ({ value: "qa-token" }) }) },
      "@/lib/server/auth": {
        ACCESS_COOKIE: "access", REFRESH_COOKIE: "refresh",
        clearAuthCookies: async () => { cleared++; }, setAuthCookies: async () => {},
      },
    }, {
      process: { env: { SPECTRAL_API_BASE_URL: "https://api.example" } },
      fetch: async (url) => {
        if (url.endsWith("/v1/auth/refresh")) { refreshed++; return Response.json({ error: "unavailable" }, { status }); }
        return Response.json({ error: "expired" }, { status: 401 });
      },
    });
    assert.equal((await backend.authorizedBackendFetch("/v1/me")).status, status);
    assert.equal(refreshed, 1);
    assert.equal(cleared, 0);
  }
});

test("an explicitly rejected refresh clears the rejected session", async () => {
  let cleared = 0;
  const backend = loadModule("../lib/server/backend.ts", {
    "server-only": {}, "next/headers": { cookies: async () => ({ get: () => ({ value: "qa-token" }) }) },
    "@/lib/server/auth": {
      ACCESS_COOKIE: "access", REFRESH_COOKIE: "refresh",
      clearAuthCookies: async () => { cleared++; }, setAuthCookies: async () => {},
    },
  }, {
    process: { env: { SPECTRAL_API_BASE_URL: "https://api.example" } },
    fetch: async () => Response.json({ error: "invalid" }, { status: 401 }),
  });
  assert.equal((await backend.authorizedBackendFetch("/v1/me")).status, 401);
  assert.equal(cleared, 1);
});

test("successful refresh preserves token rotation and retries with the new access token", async () => {
  const requests = [];
  const tokens = [];
  const backend = loadModule("../lib/server/backend.ts", {
    "server-only": {}, "next/headers": { cookies: async () => ({ get: () => ({ value: "old" }) }) },
    "@/lib/server/auth": {
      ACCESS_COOKIE: "access", REFRESH_COOKIE: "refresh",
      clearAuthCookies: async () => assert.fail("successful refresh cleared cookies"),
      setAuthCookies: async (payload) => { tokens.push(payload); },
    },
  }, {
    process: { env: { SPECTRAL_API_BASE_URL: "https://api.example" } },
    fetch: async (url, init) => {
      requests.push({ url, init });
      if (url.endsWith("/v1/auth/refresh")) return Response.json({ access_token: "new-access", refresh_token: "new-refresh" });
      return init.headers.get("Authorization") === "Bearer new-access"
        ? Response.json({ authenticated: true }) : Response.json({}, { status: 401 });
    },
  });
  assert.equal((await backend.authorizedBackendFetch("/v1/me")).status, 200);
  assert.equal(requests.length, 3);
  assert.equal(JSON.parse(requests[1].init.body).rotate_refresh_token, true);
  assert.equal(tokens[0].refresh_token, "new-refresh");
});

test("shell and route share one session read and failures can be retried", async () => {
  let calls = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const session = loadModule("../lib/client/session.ts", {
    "react": {}, "next/navigation": {},
  }, { fetch: async () => { calls++; await gate; return Response.json({ authenticated: true }); } });
  const one = session.readAuthenticatedSession();
  const two = session.readAuthenticatedSession();
  assert.equal(one, two);
  release();
  assert.equal((await one).authenticated, true);
  assert.equal(calls, 1);
  await session.readAuthenticatedSession();
  assert.equal(calls, 2);

  let failedCalls = 0;
  const unavailable = loadModule("../lib/client/session.ts", { "react": {}, "next/navigation": {} }, {
    fetch: async () => { failedCalls++; return Response.json({ authenticated: false }, { status: 503 }); },
  });
  await assert.rejects(unavailable.readAuthenticatedSession(), /Session could not be verified/);
  await assert.rejects(unavailable.readAuthenticatedSession(), /Session could not be verified/);
  assert.equal(failedCalls, 2);
});

test("account actions keep credentials and recovery diagnostics behind the BFF", async () => {
  const requests = [];
  const cookies = [];
  let cleared = 0;
  const route = loadModule("../app/api/auth/account/[action]/route.ts", {
    "next/server": {},
    "@/lib/server/auth": {
      validateMutation: async () => null,
      setAuthCookies: async (tokens) => { cookies.push(tokens); },
      clearAuthCookies: async () => { cleared++; },
    },
    "@/lib/server/backend": {
      backendFetch: async (path, init) => {
        requests.push({ path, body: JSON.parse(init.body) });
        return Response.json({ ok: true, access_token: "private-access", refresh_token: "private-refresh",
          reset_url: "private-reset", matched_user: true, delivery: { smtp_password: "private-smtp" } });
      },
      backendError: async () => "Account request is invalid.",
    },
    "@/lib/server/http": {
      noStoreJson: Response.json,
      readJsonBody: async () => ({ email: "user@example.com", role: "admin", invite_token: "private-invite", password: "private-password" }),
    },
  });
  for (const action of ["recover", "invite", "reset"]) {
    const response = await route.POST({}, { params: Promise.resolve({ action }) });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true });
  }
  assert.deepEqual(requests[0].body, { email: "user@example.com" });
  assert.equal(Object.hasOwn(requests[1].body, "role"), false);
  assert.equal(cookies[0].refresh_token, "private-refresh");
  assert.equal(cleared, 1);
  assert.equal((await route.POST({}, { params: Promise.resolve({ action: "unknown" }) })).status, 400);
  assert.equal(requests.length, 3);
});

test("account action rejects missing origin/CSRF before reading credentials or calling auth", async () => {
  const route = loadModule("../app/api/auth/account/[action]/route.ts", {
    "next/server": {},
    "@/lib/server/auth": {
      validateMutation: async () => Response.json({}, { status: 403 }),
    },
    "@/lib/server/backend": { backendFetch: async () => assert.fail("called rejected auth action") },
    "@/lib/server/http": {
      noStoreJson: Response.json,
      readJsonBody: async () => assert.fail("read rejected credentials"),
    },
  });
  assert.equal((await route.POST({}, { params: Promise.resolve({ action: "reset" }) })).status, 403);
});

test("transport failures have one safe response and retain timeout status", async () => {
  for (const expired of [false, true]) {
    const deadline = new AbortController();
    if (expired) deadline.abort();
    const backend = loadModule("../lib/server/backend.ts", {
      "server-only": {}, "next/headers": {}, "@/lib/server/auth": {},
    }, {
      process: { env: { SPECTRAL_API_BASE_URL: "https://private.internal" } },
      AbortSignal: { timeout: () => deadline.signal },
      fetch: async () => { throw new Error("private network diagnostics"); },
    });
    const response = await backend.backendFetch("/v1/portfolio");
    assert.equal(response.status, expired ? 504 : 503);
    assert.doesNotMatch(await response.text(), /private|diagnostics/);
  }
});

test("configuration errors propagate instead of becoming client validation errors", async () => {
  const backend = loadModule("../lib/server/backend.ts", {
    "server-only": {}, "next/headers": {}, "@/lib/server/auth": {},
  }, { fetch: async () => assert.fail("unconfigured request reached transport") });
  await assert.rejects(backend.backendFetch("/v1/portfolio"), /Backend is not configured/);
});

test("HTTP body parsing distinguishes malformed input, size limits and valid data", async () => {
  const http = loadModule("../lib/server/http.ts", {
    "server-only": {}, "next/server": { NextResponse: { json: Response.json } },
  });
  const request = (body, headers = {}) => new Request("https://app.example/api/example", { method: "POST", body, headers });
  assert.equal((await http.readJsonBody(request("{"))).status, 400);
  assert.equal((await http.readJsonBody(request("{}", { "content-length": "1000" }), 10)).status, 413);
  assert.equal((await http.readJsonBody(request('"ééé"'), 6)).status, 413);
  assert.equal(JSON.stringify(await http.readJsonBody(request('{"zero":0,"value":false}'))), '{"zero":0,"value":false}');
});

test("upstream JSON status and private caching survive forwarding", async () => {
  const http = loadModule("../lib/server/http.ts", {
    "server-only": {}, "next/server": { NextResponse: { json: Response.json } },
  });
  for (const status of [200, 202, 400, 401, 403, 409, 429, 503, 504]) {
    const response = await http.forwardJson(Response.json({ value: 7 }, { status }), "unavailable");
    assert.equal(response.status, status);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), { value: 7 });
  }
  assert.equal((await http.forwardJson(new Response("not JSON"), "unavailable")).status, 502);
  assert.equal((await http.forwardJson(new Response("not JSON", { status: 429 }), "unavailable")).status, 429);
});

test("a validated mutation forwards its idempotency identity and rejects invalid bodies before transport", async () => {
  const http = loadModule("../lib/server/http.ts", {
    "server-only": {}, "next/server": { NextResponse: { json: Response.json } },
  });
  const calls = [];
  const route = loadModule("../app/api/research-runs/route.ts", {
    "next/server": {}, "@/lib/server/auth": { validateMutation: async () => null },
    "@/lib/server/http": http,
    "@/lib/server/backend": { authorizedBackendFetch: async (path, options) => {
      calls.push({ path, options });
      return Response.json({ accepted: true }, { status: 202 });
    } },
  });
  const request = body => new Request("https://app.example/api/research-runs", {
    method: "POST", body, headers: { "idempotency-key": "request-identity-1" },
  });
  assert.equal((await route.POST(request("{"))).status, 400);
  assert.equal(calls.length, 0);
  assert.equal((await route.POST(request('{"query":"Investigate the supplied evidence"}'))).status, 202);
  assert.equal(calls[0].options.headers["Idempotency-Key"], "request-identity-1");
  assert.equal(JSON.parse(calls[0].options.body).query, "Investigate the supplied evidence");
});
