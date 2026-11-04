"""Owned X browser access and serialized, durably paced post delivery.

The account lease and timing records live in PostgreSQL, shared by every
publisher. Credentials and the browser-state encryption key stay in Key Vault;
encrypted cookies live with the owned account record. No research or
content generation runs inside this transport.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, asynccontextmanager
from datetime import datetime, timezone
import asyncio
import json
import logging
import platform
import threading
import time
import uuid
from typing import Any, Iterator, AsyncIterator
from urllib.parse import urlparse

from azure.core.exceptions import ResourceNotFoundError
from azure.keyvault.secrets import SecretClient
from cryptography.fernet import Fernet

from services.secrets import build_azure_credential, runtime_env_value


# Explicit search/open-post operations. Browser-generated HTTP traffic shares
# the separate network cap so its own JavaScript can finish normally.
REQUEST_INTERVAL_SECONDS = 1.0
NETWORK_INTERVAL_SECONDS = 0.1
NAVIGATION_INTERVAL_SECONDS = 10.0
POST_INTERVAL_SECONDS = 120.0
LOGIN_INTERVAL_SECONDS = 900.0
SECTIONS = ("top_events", "must_read_movers", "unresolved_large_moves")


class XPublishingError(RuntimeError):
    pass


class XAccountBusy(XPublishingError):
    pass


class XVerificationRequired(XPublishingError):
    pass


class XSubmissionUncertain(XPublishingError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vault() -> SecretClient:
    url = runtime_env_value("AZURE_KEY_VAULT_URL")
    name = runtime_env_value("KEYVAULT_NAME") or runtime_env_value("AZURE_KEY_VAULT_NAME")
    if not url and name:
        url = f"https://{name}.vault.azure.net"
    if not url:
        raise XPublishingError("X account Key Vault is not configured.")
    return SecretClient(vault_url=url, credential=build_azure_credential())


def configured_x_account() -> str:
    account = runtime_env_value("X_ACCOUNT_USERNAME")
    if not account:
        name = runtime_env_value("X_USERNAME_SECRET_NAME") or "twitter-username"
        account = _vault().get_secret(name).value
    if not account:
        raise XPublishingError("The X account is not configured.")
    return account.strip().removeprefix("@")


class XAccountStore:
    """A renewable account lease, database-clock rate limits and post receipts.

    The account key excludes environment so every worker shares the same caps.
    SQL transactions release their connection before the browser waits.
    """

    def __init__(self, account: str):
        self.account = account.removeprefix("@").strip()
        if not self.account or not self.account.replace("_", "").isalnum():
            raise XPublishingError("Invalid X account identity.")
        self.owner = str(uuid.uuid4())
        self._failed = threading.Event()
        self._stop = threading.Event()
        self._active = False
        self._lease_deadline = 0.0
        self._renewal: threading.Thread | None = None

    @contextmanager
    def _db(self) -> Iterator[Any]:
        from services.pipeline_store import open_database_connection
        connection = open_database_connection()
        if connection is None:
            raise XPublishingError("X delivery database is unavailable.")
        with closing(connection):
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()

    def _ensure_schema(self) -> None:
        with self._db() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS x_publishing_accounts (
                account TEXT PRIMARY KEY, owner TEXT, lease_until TIMESTAMPTZ,
                next_request_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
                next_post_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
                next_login_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity')""")
            cur.execute("""ALTER TABLE x_publishing_accounts
                ADD COLUMN IF NOT EXISTS next_network_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
                ADD COLUMN IF NOT EXISTS next_navigation_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
                ADD COLUMN IF NOT EXISTS session_ciphertext TEXT""")
            cur.execute("""CREATE TABLE IF NOT EXISTS x_publishing_posts (
                account TEXT NOT NULL, event_id TEXT NOT NULL, payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                PRIMARY KEY(account,event_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS x_publishing_batches (
                account TEXT NOT NULL, source_version TEXT NOT NULL, payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                PRIMARY KEY(account,source_version))""")

    def _check(self) -> None:
        if not self._active or self._failed.is_set() or time.monotonic() >= self._lease_deadline:
            raise XPublishingError("X account lease is unavailable; browser access stopped.")

    def _renew(self) -> None:
        started=time.monotonic()
        with self._db() as cur:
            cur.execute("""UPDATE x_publishing_accounts
                SET lease_until=clock_timestamp()+interval '60 seconds'
                WHERE account=%s AND owner=%s AND lease_until>clock_timestamp()
                RETURNING account""", (self.account.lower(), self.owner))
            if cur.fetchone() is None:
                raise XPublishingError("X account lease expired.")
        self._lease_deadline=started+50

    def __enter__(self) -> XAccountStore:
        self._ensure_schema()
        started=time.monotonic()
        with self._db() as cur:
            cur.execute("""INSERT INTO x_publishing_accounts(account,owner,lease_until)
                VALUES(%s,%s,clock_timestamp()+interval '60 seconds')
                ON CONFLICT(account) DO UPDATE SET owner=excluded.owner,lease_until=excluded.lease_until
                WHERE x_publishing_accounts.lease_until IS NULL
                   OR x_publishing_accounts.lease_until<clock_timestamp()
                RETURNING account""", (self.account.lower(), self.owner))
            if cur.fetchone() is None:
                raise XAccountBusy("Another process owns this X account.")
        self._active = True
        self._lease_deadline=started+50
        def renew() -> None:
            while not self._stop.wait(20):
                try:
                    self._renew()
                except Exception:
                    self._failed.set()
                    return
        self._renewal = threading.Thread(target=renew, name="x-account-lease", daemon=True)
        self._renewal.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._renewal:
            self._renewal.join(timeout=5)
        if self._active:
            try:
                with self._db() as cur:
                    cur.execute("""UPDATE x_publishing_accounts SET owner=NULL,lease_until=NULL,
                        next_network_at=greatest(next_network_at,clock_timestamp()+interval '1 second')
                        WHERE account=%s AND owner=%s""", (self.account.lower(), self.owner))
            finally:
                self._active = False

    def permit(self, kind: str) -> None:
        """Atomic database-clock reservation before every actual attempt."""
        intervals = {"network": NETWORK_INTERVAL_SECONDS, "navigation": NAVIGATION_INTERVAL_SECONDS,
                     "request": REQUEST_INTERVAL_SECONDS, "post": POST_INTERVAL_SECONDS,
                     "login": LOGIN_INTERVAL_SECONDS}
        interval = intervals[kind]
        column = "next_" + kind + "_at"  # kind is validated above, never user SQL.
        while True:
            self._check()
            with self._db() as cur:
                cur.execute(f"""UPDATE x_publishing_accounts
                    SET {column}=clock_timestamp()+(%s * interval '1 second')
                    WHERE account=%s AND owner=%s AND lease_until>clock_timestamp()
                      AND {column}<=clock_timestamp() RETURNING account""",
                    (interval, self.account.lower(), self.owner))
                if cur.fetchone() is not None:
                    return
                cur.execute(f"""SELECT extract(epoch from ({column}-clock_timestamp())),
                    owner=%s AND lease_until>clock_timestamp()
                    FROM x_publishing_accounts WHERE account=%s""", (self.owner,self.account.lower()))
                row=cur.fetchone()
                if not row or not row[1]:
                    self._failed.set()
                    raise XPublishingError("X account lease was lost before a request.")
                delay=max(float(row[0]),0.01)
            time.sleep(min(delay,0.25))

    def mark_post_dispatched(self) -> None:
        self._check()
        with self._db() as cur:
            cur.execute("""UPDATE x_publishing_accounts
                SET next_post_at=greatest(next_post_at,clock_timestamp()+(%s*interval '1 second'))
                WHERE account=%s AND owner=%s AND lease_until>clock_timestamp()
                RETURNING account""", (POST_INTERVAL_SECONDS,self.account.lower(),self.owner))
            if cur.fetchone() is None:
                raise XPublishingError("X account lease was lost after dispatch.")

    def read_post(self, event_id: str) -> dict[str, Any] | None:
        self._check()
        with self._db() as cur:
            cur.execute("SELECT payload FROM x_publishing_posts WHERE account=%s AND event_id=%s",
                        (self.account.lower(),event_id))
            row=cur.fetchone()
            return dict(row[0]) if row else None

    def save_post(self, record: dict[str, Any]) -> None:
        self._check()
        with self._db() as cur:
            cur.execute("""INSERT INTO x_publishing_posts(account,event_id,payload)
                SELECT %s,%s,%s::jsonb WHERE EXISTS(SELECT 1 FROM x_publishing_accounts
                    WHERE account=%s AND owner=%s AND lease_until>clock_timestamp())
                ON CONFLICT(account,event_id) DO UPDATE SET payload=excluded.payload,updated_at=clock_timestamp()
                WHERE x_publishing_posts.payload->>'text'=excluded.payload->>'text'
                  AND (NOT (x_publishing_posts.payload ? 'media')
                       OR x_publishing_posts.payload->'media'=excluded.payload->'media')
                  AND (x_publishing_posts.payload->>'status'<>'published'
                       OR excluded.payload->>'status'='published')
                RETURNING event_id""", (self.account.lower(),record["event_id"],json.dumps(record),
                    self.account.lower(),self.owner))
            if cur.fetchone() is None:
                raise XPublishingError("Post receipt write refused: lease or immutable event content changed.")

    def records(self) -> list[dict[str, Any]]:
        """Read-only account history; never owns browser access."""
        self._ensure_schema()
        with self._db() as cur:
            cur.execute("SELECT payload FROM x_publishing_posts WHERE account=%s ORDER BY updated_at DESC",
                        (self.account.lower(),))
            return [dict(row[0]) for row in cur.fetchall()]

    def enqueue_batch(self, source_version: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Retain new events without needing the browser's exclusive lease.

        Queue creation cannot rewrite an existing event or submission receipt.
        Only the browser owner can advance delivery state through save_post.
        """
        self._ensure_schema()
        with self._db() as cur:
            for record in records:
                if record.get("status") != "prepared" or not record.get("event_id") or not record.get("text"):
                    raise XPublishingError("Only prepared events can enter the delivery queue.")
                cur.execute("""INSERT INTO x_publishing_posts(account,event_id,payload)
                    VALUES(%s,%s,%s::jsonb) ON CONFLICT(account,event_id) DO NOTHING""",
                    (self.account.lower(), record["event_id"], json.dumps(record)))
            cur.execute("SELECT event_id,payload FROM x_publishing_posts WHERE account=%s AND event_id=ANY(%s)",
                        (self.account.lower(), [record["event_id"] for record in records]))
            stored = {event_id: dict(payload) for event_id, payload in cur.fetchall()}
            batch = {"status": "prepared" if records else "empty", "source_version": source_version,
                     "composition": "attention_event_images_link",
                     "posts": [stored[record["event_id"]] for record in records]}
            cur.execute("""INSERT INTO x_publishing_batches(account,source_version,payload)
                VALUES(%s,%s,%s::jsonb) ON CONFLICT(account,source_version) DO UPDATE
                SET payload=excluded.payload,updated_at=clock_timestamp()""",
                (self.account.lower(), source_version, json.dumps(batch)))
        return batch

    def session(self, cipher: Fernet) -> dict[str, Any] | None:
        self._check()
        with self._db() as cur:
            cur.execute("SELECT session_ciphertext FROM x_publishing_accounts WHERE account=%s",
                        (self.account.lower(),))
            value=cur.fetchone()[0]
            return json.loads(cipher.decrypt(value.encode())) if value else None

    def save_session(self, state: dict[str, Any], cipher: Fernet) -> None:
        self._check()
        encrypted=cipher.encrypt(json.dumps(state,separators=(",",":")).encode()).decode()
        with self._db() as cur:
            cur.execute("""UPDATE x_publishing_accounts SET session_ciphertext=%s
                WHERE account=%s AND owner=%s AND lease_until>clock_timestamp() RETURNING account""",
                (encrypted,self.account.lower(),self.owner))
            if cur.fetchone() is None:
                raise XPublishingError("The account lease was lost before browser-state persistence.")

class XBrowser:
    """Full JavaScript browser with nonblocking, serialized request pacing."""

    def __init__(self, store: XAccountStore, *, headless: bool = True):
        self.store = store
        self.vault = _vault()
        key_name = runtime_env_value("X_SESSION_ENCRYPTION_KEY_SECRET_NAME") or "twitter-browser-encryption-key"
        self.cipher = Fernet((self.vault.get_secret(key_name).value or "").encode("ascii"))
        self.headless = headless
        self.session: Any = None
        self.page: Any = None
        self._submitting: dict[str, Any] | None = None
        self._submission_sent = False
        self._authenticated = False
        self._dispatch_lock = asyncio.Lock()
        self._request_times: list[float] = []
        self._navigation_times: list[float] = []
        self._last_post_release = 0.0
        self._post_network_failure: str | None = None
        self._closing = False

    async def _guard_request(self, route: Any) -> None:
        try:
            async with self._dispatch_lock:
                request = route.request
                operation = urlparse(request.url).path.rsplit("/", 1)[-1]
                posting = request.method == "POST" and operation in {"CreateTweet", "CreateNoteTweet"}
                if posting:
                    body = request.post_data_json or {}
                    text = (body.get("variables") or {}).get("tweet_text")
                    if not self._submitting or self._submission_sent or text != self._submitting["text"]:
                        raise XPublishingError("An unowned or repeated post request was blocked.")
                    if self._submitting.get("media_required"):
                        entities = ((body.get("variables") or {}).get("media") or {}).get("media_entities") or []
                        ids = [str(item.get("media_id") or "") for item in entities]
                        if (not 1 <= len(ids) <= 4 or len(ids) != len(self._submitting.get("media") or [])
                                or len(set(ids)) != len(ids) or not all(item.isdigit() for item in ids)):
                            raise XPublishingError("The post request is missing its owned chart attachments.")
                        self._submitting["x_uploaded_media_ids"] = ids
                navigating = request.is_navigation_request() and request.frame.parent_frame is None
                if navigating:
                    await asyncio.to_thread(self.store.permit, "navigation")
                deadline = self._request_times[-1] + NETWORK_INTERVAL_SECONDS if self._request_times else 0
                if navigating and self._navigation_times:
                    deadline = max(deadline, self._navigation_times[-1] + NAVIGATION_INTERVAL_SECONDS)
                if posting and self._last_post_release:
                    deadline = max(deadline, self._last_post_release + POST_INTERVAL_SECONDS)
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                if posting:
                    self._submitting.update(status="submitting", submitted_at=_now())
                    await asyncio.to_thread(self.store.save_post, self._submitting)
                    self._submission_sent = True
                self.store._check()
                released = time.monotonic()
                self._request_times.append(released)
                if navigating:
                    self._navigation_times.append(released)
                if posting:
                    self._last_post_release = released
                await route.continue_()
                if posting:
                    await asyncio.to_thread(self.store.mark_post_dispatched)
        except Exception as exc:
            if not self._closing:
                logging.getLogger(__name__).warning("X request stopped (%s)", type(exc).__name__)
            try:
                await route.abort()
            except Exception:
                pass

    async def __aenter__(self) -> XBrowser:
        from scrapling.fetchers import AsyncStealthySession
        state = await asyncio.to_thread(self.store.session, self.cipher)
        if state is None:
            secret_name = runtime_env_value("X_BROWSER_SESSION_SECRET_NAME") or "twitter-browser-session"
            try:
                secret = await asyncio.to_thread(self.vault.get_secret, secret_name)
                state = json.loads(secret.value or "{}")
            except ResourceNotFoundError:
                state = {}
        self.session = AsyncStealthySession(
            headless=self.headless, real_chrome=platform.system() in {"Darwin", "Windows"},
            cookies=state.get("cookies") or [], google_search=False, solve_cloudflare=True,
            retries=1, timeout=180_000, additional_args={"service_workers": "block"},
        )
        try:
            await self.session.start()
            context = self.session.context
            if state.get("origins"):
                await context.add_init_script("""(states => {
                    for (const state of states) {
                        if (location.origin !== state.origin || sessionStorage.getItem('sn_x_restored')) continue;
                        for (const item of state.localStorage || []) localStorage.setItem(item.name,item.value);
                        sessionStorage.setItem('sn_x_restored','1');
                    }
                })(""" + json.dumps(state["origins"]) + ");")
            await context.route("**/*", self._guard_request)
            async def close_socket(socket: Any) -> None:
                await socket.close()
            await context.route_web_socket("**/*", close_socket)
            def failed(request: Any) -> None:
                if request.method == "POST" and urlparse(request.url).path.rsplit("/", 1)[-1] in {"CreateTweet", "CreateNoteTweet"}:
                    self._post_network_failure = request.failure
            async def configure_page(page: Any) -> None:
                self.page = page
                page.set_default_timeout(90_000)
                page.on("requestfailed", failed)
            await configure_page(await context.new_page())
            await asyncio.to_thread(self.store.permit, "network")
            await self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=180_000)
            if (await self.page.title() == "Just a moment..."
                    or await self.page.locator('input[name="cf-turnstile-response"]').count()):
                # Raw Page.goto bypasses Scrapling's challenge handling. Use its
                # public fetch API to establish clearance in this same context.
                await self.page.close()
                await asyncio.wait_for(self.session.fetch(
                    "https://x.com/home", page_setup=configure_page, solve_cloudflare=True,
                ), timeout=120)
                await configure_page(await context.new_page())
                await self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=180_000)
            await self._authenticate()
            await self._save_session()
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_: Any) -> None:
        if self.session:
            try:
                if self._authenticated:
                    try:
                        await self._save_session()
                    except Exception as exc:
                        logging.getLogger(__name__).warning("X session refresh unavailable (%s)", type(exc).__name__)
            finally:
                self._closing = True
                await self.session.close()
                self.session = None

    async def _authenticate(self) -> None:
        if (await self.page.title() == "Just a moment..."
                or await self.page.locator('input[name="cf-turnstile-response"]').count()):
            raise XVerificationRequired("X verification did not clear; sign-in was not attempted.")
        profile = self.page.get_by_role("link", name="Profile", exact=True)
        try:
            await profile.wait_for(timeout=30_000)
        except Exception:
            await asyncio.to_thread(self.store.permit, "login")
            await self.page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=180_000)
            try:
                username = (await asyncio.to_thread(self.vault.get_secret, runtime_env_value("X_USERNAME_SECRET_NAME") or "twitter-username")).value
                password = (await asyncio.to_thread(self.vault.get_secret, runtime_env_value("X_PASSWORD_SECRET_NAME") or "twitter-password")).value
                if not username or not password or username.lower().removeprefix("@") != self.store.account.lower():
                    raise XPublishingError("Key Vault credentials do not match the selected X account.")
                await self.page.locator('input[name="username_or_email"]').fill(username)
                await self.page.get_by_role("button", name="Continue", exact=True).click()
                await self.page.locator('input[name="password"][autocomplete="current-password"]').fill(password)
                await self.page.get_by_role("button", name="Continue", exact=True).click()
                await profile.wait_for(timeout=90_000)
            except Exception as exc:
                raise XVerificationRequired("X sign-in needs attention.") from exc
        if str(await profile.get_attribute("href") or "").lower() != "/" + self.store.account.lower():
            raise XPublishingError("The browser signed in to a different X account.")
        self._authenticated = True

    async def _save_session(self) -> None:
        state = await self.page.context.storage_state()
        state["cookies"] = [item for item in state["cookies"] if item["domain"].lstrip(".") in {"x.com", "twitter.com"}]
        state["origins"] = [item for item in state["origins"] if item["origin"] in {"https://x.com", "https://twitter.com"}]
        await asyncio.to_thread(self.store.save_session, state, self.cipher)

    async def _open_composer(self) -> None:
        if urlparse(self.page.url).path != "/compose/post":
            await self.page.get_by_role("link", name="Post", exact=True).click()
        await self.page.get_by_role("dialog").get_by_role("textbox", name="Post text", exact=True).wait_for()

    async def inspect_composer(self) -> dict[str, Any]:
        await self._open_composer()
        return {"url": self.page.url, "buttons": await self.page.get_by_role("button").all_text_contents(),
                "request_count": len(self._request_times)}

    async def read_posts(self) -> list[dict[str, Any]]:
        articles = self.page.locator('article[data-testid="tweet"]')
        try:
            await articles.first.wait_for(timeout=45_000)
        except Exception:
            retry = self.page.get_by_role("button", name="Retry", exact=True)
            if await retry.count() == 1:
                await retry.click()
                await articles.first.wait_for(timeout=45_000)
            elif await self.page.get_by_text("No results for", exact=False).count():
                return []
            else:
                raise
        return await articles.evaluate_all("""els => els.map(el => ({
            text: el.querySelector('[data-testid="tweetText"]')?.textContent || '',
            url: el.querySelector('a:has(time)')?.href || '',
            published_at: el.querySelector('time')?.getAttribute('datetime') || '',
            author: el.querySelector('[data-testid="User-Name"]')?.innerText || '',
            truncated: Boolean(el.querySelector('[data-testid="tweet-text-show-more-link"]')),
            quoted_text: Array.from(el.querySelectorAll('[data-testid="tweetText"]')).slice(1).map(e => e.textContent)
        })).filter(post => post.url)""")

    async def search(self, query: str, *, mode: str = "latest", limit: int = 12) -> list[dict[str, Any]]:
        from urllib.parse import urlencode
        if mode not in {"top", "latest"}:
            raise XPublishingError("Choose top or latest X search results.")
        await asyncio.to_thread(self.store.permit, "request")
        url = "https://x.com/search?" + urlencode({"q": query, "src": "typed_query", "f": "live" if mode == "latest" else "top"})
        await self.page.goto(url, wait_until="domcontentloaded", timeout=180_000)
        posts: dict[str, dict[str, Any]] = {}
        for _ in range(3):
            captured = await self.read_posts()
            for post in captured:
                posts[post["url"]] = {**post, "search_mode": mode, "captured_at": _now()}
            if len(posts) >= limit or not captured:
                break
            await self.page.mouse.wheel(0, 900)
            await self.page.wait_for_timeout(1500)
        return list(posts.values())[:limit]

    async def profile_posts(self) -> list[dict[str, Any]]:
        await self.page.get_by_role("link", name="Profile", exact=True).click()
        return await self.read_posts()

    async def read_post(self, url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if (parsed.scheme != "https" or parsed.netloc not in {"x.com", "www.x.com", "twitter.com"}
                or len(parts) != 3 or parts[1] != "status" or not parts[2].isdigit()):
            raise XPublishingError("An HTTPS X post URL is required.")
        await asyncio.to_thread(self.store.permit, "request")
        await self.page.goto("https://x.com" + parsed.path, wait_until="domcontentloaded", timeout=180_000)
        posts = await self.read_posts()
        for post in posts:
            if urlparse(post["url"]).path.rstrip("/").endswith("/status/" + parts[2]):
                return {**post, "captured_at": _now()}
        raise XPublishingError("The selected X post was not readable.")

    async def publish(self, record: dict[str, Any]) -> dict[str, Any]:
        existing = await asyncio.to_thread(self.store.read_post, record["event_id"])
        if existing and existing.get("status") == "published":
            return existing
        if existing and existing.get("status") in {"submitting", "uncertain"}:
            raise XSubmissionUncertain("This event has an unresolved submission; automatic retry is blocked.")
        if existing and existing.get("text") != record["text"]:
            raise XPublishingError("A prepared event cannot be rewritten during delivery.")
        if existing and existing.get("media") and existing["media"] != record.get("media"):
            raise XPublishingError("Prepared chart images cannot be replaced during delivery.")
        linked = record.get("composition") == "attention_event_images_link"
        if linked and (record.get("text") != record.get("source_title", "") + "\n\n" + record.get("attention_url", "")
                       or not record.get("attention_url") or len(record.get("source_title", "")) > 255):
            raise XPublishingError("The Attention headline and saved-page link must match the prepared post.")
        if not record.get("text", "").strip() or (not linked and len(record["text"]) > 280):
            raise XPublishingError("A standalone post of at most 280 characters is required.")
        button = await self.prepare_composer(record)
        prepared = {**record, "account": self.store.account, "status": "prepared", "prepared_at": _now()}
        await asyncio.to_thread(self.store.save_post, prepared)
        await asyncio.to_thread(self.store.permit, "post")
        prepared.update(status="submitting", submission_started_at=_now())
        await asyncio.to_thread(self.store.save_post, prepared)
        self._submitting = prepared
        self._submission_sent = False
        self._post_network_failure = None
        try:
            async with self.page.expect_response(lambda response: response.request.method == "POST"
                    and urlparse(response.url).path.rsplit("/", 1)[-1] in {"CreateTweet", "CreateNoteTweet"},
                    timeout=120_000) as pending:
                await button.click(timeout=120_000)
            response = await pending.value
            payload = await response.json()
            data = payload.get("data", {}).get("create_tweet", {}).get("tweet_results", {}).get("result", {})
            post_id = str(data.get("rest_id") or "")
            if not response.ok or not post_id.isdigit() or payload.get("errors"):
                raise XSubmissionUncertain("X did not return an unambiguous post receipt.")
            result = {**prepared, "status": "published", "x_post_id": post_id,
                      "x_post_url": f"https://x.com/{self.store.account}/status/{post_id}", "published_at": _now()}
            result["x_media"] = (data.get("legacy") or {}).get("extended_entities", {}).get("media", [])
            await asyncio.to_thread(self.store.save_post, result)
        except BaseException as exc:
            await asyncio.to_thread(self.store.save_post, {
                **prepared, "status": "uncertain", "error_type": type(exc).__name__,
                "network_failure": self._post_network_failure,
            })
            raise XSubmissionUncertain("Submission is unconfirmed; this event will not be retried automatically.") from exc
        finally:
            self._submitting = None
        try:
            await self._save_session()
        except Exception:
            result["session_refresh"] = "failed"
        return result

    async def prepare_composer(self, record: dict[str, Any]) -> Any:
        """Fill the owned draft and upload retained images without submitting it."""
        from patchright.async_api import expect
        await self._open_composer()
        composer = self.page.get_by_role("textbox", name="Post text", exact=True)
        dialog = self.page.get_by_role("dialog").filter(has=composer).last
        await composer.fill(record["text"])
        if (await composer.inner_text()).replace("\r\n", "\n") != record["text"].replace("\r\n", "\n"):
            raise XPublishingError("The composer does not contain the exact prepared post.")
        if record.get("media_required"):
            from services.attention_x_media import load_attention_images
            files = await asyncio.to_thread(load_attention_images, record, self.store.account)
            await dialog.locator('input[type="file"]').set_input_files(files)
            await expect(dialog.locator('[data-testid="attachments"] img')).to_have_count(len(files), timeout=90_000)
        button = dialog.get_by_role("button", name="Post", exact=True)
        await expect(button).to_be_enabled(timeout=90_000)
        return button


@asynccontextmanager
async def open_x_account(account: str, *, headless: bool = True) -> AsyncIterator[XBrowser]:
    store = XAccountStore(account)
    # Parallel investigations share one account. Waiting yields to the caller's
    # acquisition deadline; cancellation before ownership leaves no live lease.
    while True:
        try:
            store.__enter__()
            break
        except XAccountBusy:
            await asyncio.sleep(1)
    try:
        async with XBrowser(store, headless=headless) as browser:
            yield browser
    finally:
        store.__exit__(None, None, None)
