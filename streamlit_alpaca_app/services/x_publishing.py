"""Owned X browser access and serialized, durably paced post delivery.

The account lease and timing records live in PostgreSQL, shared by every
publisher. Credentials and the browser-state encryption key stay in Key Vault;
encrypted cookies live with the owned account record. No research or
content generation runs inside this transport.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import logging
import platform
import threading
import time
import uuid
from typing import Any, Iterator
from urllib.parse import urlparse

from azure.core.exceptions import ResourceNotFoundError
from azure.keyvault.secrets import SecretClient
from cryptography.fernet import Fernet

from services.secrets import build_azure_credential, runtime_env_value


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
                ADD COLUMN IF NOT EXISTS attention_started_at TIMESTAMPTZ,
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
                  AND (x_publishing_posts.payload->>'status'<>'published'
                       OR excluded.payload->>'status'='published')
                RETURNING event_id""", (self.account.lower(),record["event_id"],json.dumps(record),
                    self.account.lower(),self.owner))
            if cur.fetchone() is None:
                raise XPublishingError("Post receipt write refused: lease or immutable event content changed.")

    def records(self) -> list[dict[str, Any]]:
        """Read-only history for the shared editor; never owns browser access."""
        self._ensure_schema()
        with self._db() as cur:
            cur.execute("SELECT payload FROM x_publishing_posts WHERE account=%s ORDER BY updated_at DESC",
                        (self.account.lower(),))
            return [dict(row[0]) for row in cur.fetchall()]

    def read_batch(self, source_version: str) -> dict[str, Any] | None:
        self._check()
        with self._db() as cur:
            cur.execute("SELECT payload FROM x_publishing_batches WHERE account=%s AND source_version=%s",
                        (self.account.lower(),source_version))
            row=cur.fetchone()
            return dict(row[0]) if row else None

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

    def save_batch(self, source_version: str, payload: dict[str, Any]) -> None:
        self._check()
        with self._db() as cur:
            cur.execute("""INSERT INTO x_publishing_batches(account,source_version,payload)
                SELECT %s,%s,%s::jsonb WHERE EXISTS(SELECT 1 FROM x_publishing_accounts
                    WHERE account=%s AND owner=%s AND lease_until>clock_timestamp())
                ON CONFLICT(account,source_version) DO UPDATE
                SET payload=excluded.payload,updated_at=clock_timestamp() RETURNING source_version""",
                (self.account.lower(),source_version,json.dumps(payload),self.account.lower(),self.owner))
            if cur.fetchone() is None:
                raise XPublishingError("The preparation lease was lost before saving its batch.")


class XBrowser:
    """Full JavaScript browser; every page request is paced before release."""

    def __init__(self, store: XAccountStore, *, headless: bool = True):
        self.store = store
        self.vault = _vault()
        key_name=runtime_env_value("X_SESSION_ENCRYPTION_KEY_SECRET_NAME") or "twitter-browser-encryption-key"
        self.cipher=Fernet((self.vault.get_secret(key_name).value or "").encode("ascii"))
        self.headless = headless
        self.session: Any = None
        self.page: Any = None
        self._submitting: dict[str, Any] | None = None
        self._submission_sent = False
        self._authenticated = False
        self._request_count = 0
        self._request_times: list[float] = []
        self._data_times: list[float] = []
        self._navigation_times: list[float] = []
        self._last_post_release = 0.0

    def _guard_request(self, route: Any) -> None:
        try:
            request = route.request
            operation = urlparse(request.url).path.rsplit("/", 1)[-1]
            posting = request.method == "POST" and operation in {"CreateTweet", "CreateNoteTweet"}
            if posting:
                if not self._submitting or self._submission_sent:
                    raise XPublishingError("An unowned or repeated post request was blocked.")
            navigating = request.is_navigation_request()
            data_request = request.resource_type in {"xhr", "fetch"} or posting
            if navigating:
                self.store.permit("navigation")
            if data_request:
                self.store.permit("request")
            # The exclusive renewable account lease serializes all browser
            # traffic. Static assets use its local monotonic clock, avoiding a
            # database connection for each image/script. Database-clock permits
            # still govern navigations, data requests and post submissions.
            deadline = (self._request_times[-1] + NETWORK_INTERVAL_SECONDS) if self._request_times else 0
            if data_request and self._data_times:
                deadline = max(deadline,self._data_times[-1]+REQUEST_INTERVAL_SECONDS)
            if navigating and self._navigation_times:
                deadline = max(deadline,self._navigation_times[-1]+NAVIGATION_INTERVAL_SECONDS)
            if posting and self._last_post_release:
                deadline = max(deadline, self._last_post_release + POST_INTERVAL_SECONDS)
            while time.monotonic() < deadline:
                time.sleep(min(deadline-time.monotonic(),0.1))
            if posting:
                self._submitting.update(status="submitting", submitted_at=_now())
                self.store.save_post(self._submitting)
                self._submission_sent = True
            self.store._check()
            self._request_count += 1
            self._request_times.append(time.monotonic())
            if data_request:
                self._data_times.append(self._request_times[-1])
            if navigating:
                self._navigation_times.append(self._request_times[-1])
            if posting:
                self._last_post_release = self._request_times[-1]
            route.continue_()
            if posting:
                self.store.mark_post_dispatched()
        except BaseException:
            route.abort()
            raise

    def __enter__(self) -> XBrowser:
        from scrapling.fetchers import StealthySession
        secret_name = runtime_env_value("X_BROWSER_SESSION_SECRET_NAME") or "twitter-browser-session"
        state = self.store.session(self.cipher)
        if state is None:
            try:
                state = json.loads(self.vault.get_secret(secret_name).value or "{}")
            except ResourceNotFoundError:
                state = {}
        self.session = StealthySession(
            headless=self.headless, real_chrome=platform.system() in {"Darwin","Windows"}, cookies=state.get("cookies") or [],
            google_search=False, solve_cloudflare=False, retries=1, timeout=300_000,
            additional_args={"service_workers": "block"},
        )
        try:
            self.session.start()
            context = self.session.context
            if state.get("origins"):
                # A Playwright storage state includes local storage as well as
                # cookies. Restore it before X's own JavaScript initializes.
                context.add_init_script("""(states => {
                    for (const state of states) {
                        if (location.origin !== state.origin || sessionStorage.getItem('sn_x_restored')) continue;
                        for (const item of state.localStorage || []) localStorage.setItem(item.name,item.value);
                        sessionStorage.setItem('sn_x_restored','1');
                    }
                })(""" + json.dumps(state["origins"]) + ");")
            context.route("**/*", self._guard_request)
            # No service-worker or WebSocket path may send unmetered traffic.
            context.route_web_socket("**/*", lambda socket: socket.close())
            self.page = context.new_page()
            self.page.set_default_timeout(120_000)
            self.store.permit("network")
            self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=300_000)
            self._authenticate()
            self._save_session()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_: Any) -> None:
        if self.session:
            try:
                if self._authenticated:
                    try:
                        self._save_session()
                    except Exception as exc:
                        logging.getLogger(__name__).warning("X session refresh unavailable (%s)",type(exc).__name__)
            finally:
                self.session.close()
                self.session = None

    def _authenticate(self) -> None:
        profile = self.page.get_by_role("link", name="Profile", exact=True)
        try:
            profile.wait_for(timeout=30_000)
        except Exception:
            self.store.permit("login")
            self.page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=300_000)
            try:
                username = self.vault.get_secret(runtime_env_value("X_USERNAME_SECRET_NAME") or "twitter-username").value
                password = self.vault.get_secret(runtime_env_value("X_PASSWORD_SECRET_NAME") or "twitter-password").value
                if not username or not password or username.lower().removeprefix("@") != self.store.account.lower():
                    raise XPublishingError("Key Vault credentials do not match the selected X account.")
                self.page.locator('input[name="username_or_email"]').fill(username)
                self.page.get_by_role("button", name="Continue", exact=True).click()
                self.page.locator('input[name="password"][autocomplete="current-password"]').fill(password)
                self.page.get_by_role("button", name="Continue", exact=True).click()
                profile.wait_for(timeout=120_000)
            except Exception as exc:
                raise XVerificationRequired("X sign-in needs attention. No challenge or account restriction was bypassed.") from exc
        if str(profile.get_attribute("href") or "").lower() != "/" + self.store.account.lower():
            raise XPublishingError("The browser signed in to a different X account.")
        self._authenticated = True

    def _save_session(self) -> None:
        state = self.page.context.storage_state()
        state["cookies"] = [item for item in state["cookies"]
                            if item["domain"].lstrip(".") in {"x.com", "twitter.com"}]
        state["origins"] = [item for item in state["origins"]
                            if item["origin"] in {"https://x.com", "https://twitter.com"}]
        self.store.save_session(state,self.cipher)

    def inspect_composer(self) -> dict[str, Any]:
        self._open_composer()
        return {"url": self.page.url, "textboxes": self.page.get_by_role("textbox").evaluate_all(
                    "els => els.map(e => ({role:e.getAttribute('role'),label:e.getAttribute('aria-label'),testid:e.getAttribute('data-testid')}))"),
                "buttons": self.page.get_by_role("button").all_text_contents(),
                "request_count": self._request_count,
                "minimum_request_gap_seconds": min((b-a for a,b in zip(self._request_times,self._request_times[1:])),default=None)}

    def _open_composer(self) -> None:
        if urlparse(self.page.url).path != "/compose/post":
            # Use X's own navigation so its JavaScript and loaded assets are
            # retained between posts, as in an ordinary browser session.
            self.page.get_by_role("link",name="Post",exact=True).click()
        self.page.get_by_role("dialog").get_by_role("textbox",name="Post text",exact=True).wait_for()

    def publish(self, record: dict[str, Any]) -> dict[str, Any]:
        existing = self.store.read_post(record["event_id"])
        if existing and existing.get("status") == "published":
            return existing
        if existing and existing.get("status") in {"submitting", "uncertain"}:
            raise XSubmissionUncertain("This event has an unresolved submission; automatic retry is blocked.")
        if any(item.get("status") in {"submitting", "uncertain"} for item in self.store.records()):
            raise XSubmissionUncertain("The account has an unresolved submission; reconcile it before posting again.")
        if existing and existing.get("text") != record["text"]:
            raise XPublishingError("A prepared event cannot be silently rewritten during delivery.")
        if not record.get("text", "").strip() or len(record["text"]) > 280:
            raise XPublishingError("A standalone post of at most 280 characters is required.")
        self._open_composer()
        composer = self.page.get_by_role("dialog").get_by_role("textbox", name="Post text", exact=True)
        composer.fill(record["text"])
        if composer.inner_text().replace("\r\n", "\n") != record["text"].replace("\r\n", "\n"):
            raise XPublishingError("The browser composer does not contain the exact prepared post.")
        dialog = self.page.get_by_role("dialog")
        button = dialog.get_by_role("button", name="Post", exact=True)
        if not button.is_enabled():
            raise XPublishingError("X has disabled submission; check its length or account requirements.")
        prepared = {**record, "account": self.store.account, "status": "prepared", "prepared_at": _now()}
        self.store.save_post(prepared)
        # Reserve before clicking, so an unfamiliar X request/response shape
        # can never leave a potentially sent event looking safe to retry.
        self.store.permit("post")
        prepared.update(status="submitting",submission_started_at=_now())
        self.store.save_post(prepared)
        self._submitting = prepared
        self._submission_sent = False
        try:
            with self.page.expect_response(lambda response: response.request.method == "POST"
                    and urlparse(response.url).path.rsplit("/", 1)[-1] in {"CreateTweet", "CreateNoteTweet"},
                    timeout=300_000) as pending:
                button.click(timeout=300_000)
            response = pending.value
            payload = response.json()
            data = payload.get("data", {}).get("create_tweet", {}).get("tweet_results", {}).get("result", {})
            post_id = str(data.get("rest_id") or "")
            if not response.ok or not post_id.isdigit() or payload.get("errors"):
                raise XSubmissionUncertain("X did not return an unambiguous post receipt.")
            result = {**prepared, "status": "published", "x_post_id": post_id,
                      "x_post_url": f"https://x.com/{self.store.account}/status/{post_id}", "published_at": _now()}
            self.store.save_post(result)
        except BaseException as exc:
            if self._submitting:
                self.store.save_post({**prepared, "status": "uncertain", "error_type": type(exc).__name__})
                raise XSubmissionUncertain("Submission may have reached X; reconcile it before any retry.") from exc
            raise
        finally:
            self._submitting = None
        # Credential refresh is independent of an already committed receipt.
        # Its failure must never erase a known post ID or invite a second send.
        try:
            self._save_session()
        except Exception:
            result["session_refresh"] = "failed"
        return result


@contextmanager
def open_x_account(account: str, *, headless: bool = True) -> Iterator[XBrowser]:
    with XAccountStore(account) as store:
        with XBrowser(store, headless=headless) as browser:
            yield browser
