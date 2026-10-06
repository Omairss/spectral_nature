let cachedCsrfToken = "";
let csrfRequest: Promise<string> | null = null;

async function loadCsrfToken(forceRefresh = false) {
  if (!forceRefresh && cachedCsrfToken) return cachedCsrfToken;
  if (!forceRefresh && csrfRequest) return csrfRequest;
  csrfRequest = fetch("/api/auth/csrf", { cache: "no-store", credentials: "same-origin" })
    .then(async (response) => {
      if (!response.ok) throw new Error("Request verification is unavailable.");
      const payload = (await response.json()) as { csrfToken?: string };
      if (!payload.csrfToken) throw new Error("Request verification is unavailable.");
      cachedCsrfToken = payload.csrfToken;
      return cachedCsrfToken;
    })
    .finally(() => { csrfRequest = null; });
  return csrfRequest;
}

export async function csrfToken() {
  return loadCsrfToken();
}

export async function secureMutation(path: string, init: RequestInit = {}) {
  async function send(token: string) {
    const headers = new Headers(init.headers);
    headers.set("X-CSRF-Token", token);
    if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    return fetch(path, {
      ...init,
      method: init.method || "POST",
      headers,
      cache: "no-store",
      credentials: "same-origin",
    });
  }

  let response = await send(await loadCsrfToken());
  if (response.status !== 403) return response;
  const rejection = await response.clone().json().catch(() => ({})) as { error?: unknown };
  if (rejection.error !== "Request verification failed.") return response;
  cachedCsrfToken = "";
  response = await send(await loadCsrfToken(true));
  return response;
}

export async function responseError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { error?: unknown; detail?: unknown };
    const message = typeof payload.error === "string" ? payload.error : payload.detail;
    if (typeof message === "string" && message.trim()) return message.trim();
  } catch {
    // Use the stable message below.
  }
  return fallback;
}

export async function fetchDataset<T>(name: string, params: Record<string, unknown> = {}) {
  const response = await secureMutation(`/api/datasets/${encodeURIComponent(name)}`, {
    method: "POST",
    body: JSON.stringify({ params }),
  });
  if (!response.ok) throw new Error(await responseError(response, "Dataset could not be loaded."));
  return response.json() as Promise<import("@/lib/types").DatasetResponse<T>>;
}
