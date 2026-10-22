import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
function load(path, overrides) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, Response,
    require: name => Object.hasOwn(overrides, name) ? overrides[name] : require(name) });
  return module.exports;
}

function gateway(read) {
  const { loadHomePublication } = load("../lib/server/home.ts", {
    "server-only": {}, "@/lib/server/backend": { backendFetch: async () => read() },
  });
  return load("../app/api/home/route.ts", {
    "next/server": {}, "@/lib/server/home": { loadHomePublication },
    "@/lib/server/http": { noStoreJson: (body, init) => Response.json(body, init) },
  }).GET;
}
const request = (etag = "") => ({ nextUrl: new URL("https://app.example/api/home"),
  headers: new Headers(etag ? { "If-None-Match": etag } : {}) });

test("unchanged publications return a bodyless 304; independent price and Highlight changes invalidate it", async () => {
  const home = { status: "available", run_id: "same-narrative", as_of: "2026-10-22",
    beats: [{ tickers: [{ latest_close: 100 }] }], daily_datapoints: [{ value: 3 }] };
  const GET = gateway(() => Response.json(home));
  let response = await GET(request());
  let etag = response.headers.get("ETag");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-cache");
  assert.deepEqual(await response.json(), home);
  response = await GET(request(etag));
  assert.equal(response.status, 304);
  assert.equal(await response.text(), "");
  assert.equal((await GET(request(`"older", W/${etag}`))).status, 304);
  home.beats[0].tickers[0].latest_close = 101;
  response = await GET(request(etag));
  assert.equal(response.status, 200);
  assert.notEqual(response.headers.get("ETag"), etag);
  etag = response.headers.get("ETag");
  home.daily_datapoints[0].value = 4;
  response = await GET(request(etag));
  assert.equal(response.status, 200);
  assert.notEqual(response.headers.get("ETag"), etag);
});

test("upstream errors and unavailable publications never validate an old representation", async () => {
  for (const read of [() => Response.json({}, { status: 503 }),
    () => Response.json({ status: "unavailable" }), () => new Response("broken JSON")]) {
    const response = await gateway(read)(request("*"));
    assert.ok(response.status >= 500);
    assert.equal(response.headers.get("ETag"), null);
  }
});
