from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import quote
from typing import Any, Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .emailer import EmailDeliveryResult, send_email
from .pipeline_store import PipelineDataset, dataset_namespace, latest_dataset_metadata, load_latest_dataset_frame
from .secrets import build_azure_credential, resolve_secret_value


ATTENTION_HOME_JOB = "attention-home-build"
TRADING_AGENT_JOB = "trading-agent-build"
ATTENTION_HOME_DATASET = "attention_home_1d"
TRADING_AGENT_RUNS_DATASET = "trading_agent_runs"


@dataclass(frozen=True)
class SystemHealthPolicy:
    home_max_age_hours: float = 12.0
    home_due_hour_utc: int = 15
    provider_failure_window_hours: float = 6.0


@dataclass(frozen=True)
class HealthIssue:
    key: str
    title: str
    details: str
    severity: str = "critical"


@dataclass(frozen=True)
class SystemHealthResult:
    open_issues: tuple[HealthIssue, ...]
    opened_issue_keys: tuple[str, ...]
    recovered_issue_keys: tuple[str, ...]
    emails_sent: int


class SystemHealthConfigurationError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_float_env(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)


def _parse_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(min(value, maximum), minimum)


def policy_from_environment() -> SystemHealthPolicy:
    return SystemHealthPolicy(
        home_max_age_hours=_parse_float_env("SYSTEM_HEALTH_HOME_MAX_AGE_HOURS", 12.0, minimum=1.0),
        home_due_hour_utc=_parse_int_env("SYSTEM_HEALTH_HOME_DUE_HOUR_UTC", 15, minimum=0, maximum=23),
        provider_failure_window_hours=_parse_float_env(
            "SYSTEM_HEALTH_PROVIDER_FAILURE_WINDOW_HOURS", 6.0, minimum=0.25
        ),
    )


def _as_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trim(value: object, limit: int = 900) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def _home_refresh_is_due(now: datetime, due_hour_utc: int) -> bool:
    return now.weekday() < 5 and now.hour >= due_hour_utc


def _successful_horizon_count(frame: pd.DataFrame) -> int | None:
    if frame.empty or "status" not in frame.columns:
        return None
    statuses = frame["status"].astype(str).str.strip().str.lower()
    return int(statuses.isin({"ok", "success", "succeeded", "completed"}).sum())


def evaluate_system_health(
    *,
    now: datetime,
    policy: SystemHealthPolicy,
    latest_jobs: dict[str, dict[str, object]],
    home_metadata: PipelineDataset | None,
    trading_successful_horizons: int | None,
    provider_failures: list[dict[str, object]],
) -> list[HealthIssue]:
    """Evaluate durable product state without a model or market-data call."""
    issues: list[HealthIssue] = []

    attention_job = latest_jobs.get(ATTENTION_HOME_JOB)
    if attention_job and str(attention_job.get("status") or "").lower() == "failed":
        issues.append(
            HealthIssue(
                key="attention-home-build-failed",
                title="The market overview update failed",
                details=_trim(attention_job.get("error_summary") or attention_job.get("progress_message"))
                or "The latest market overview build did not complete.",
            )
        )

    if _home_refresh_is_due(now, policy.home_due_hour_utc):
        latest_home_at = _as_utc(home_metadata.ingested_at_utc) if home_metadata else None
        if latest_home_at is None or now - latest_home_at > timedelta(hours=policy.home_max_age_hours):
            details = "No current market overview dataset is available."
            if latest_home_at is not None:
                details = (
                    f"The latest market overview was written at {latest_home_at.isoformat()} "
                    f"and is older than {policy.home_max_age_hours:g} hours."
                )
            issues.append(HealthIssue(key="attention-home-stale", title="The market overview is stale", details=details))

    trading_job = latest_jobs.get(TRADING_AGENT_JOB)
    if trading_job and str(trading_job.get("status") or "").lower() == "failed":
        issues.append(
            HealthIssue(
                key="trading-agent-build-failed",
                title="The Trading Agent update failed",
                details=_trim(trading_job.get("error_summary") or trading_job.get("progress_message"))
                or "The latest Trading Agent build did not complete.",
            )
        )
    elif trading_successful_horizons == 0:
        issues.append(
            HealthIssue(
                key="trading-agent-no-successful-horizons",
                title="The Trading Agent has no usable horizon output",
                details="The latest completed Trading Agent run did not produce a successful research horizon.",
            )
        )

    for job_name, job in latest_jobs.items():
        if job.get("progress_stage") == "platform_completion_unrecorded":
            issues.append(HealthIssue(
                key=f"pipeline-job-unrecorded-{job_name}",
                title=f"Completion was not recorded for {job_name}",
                details=_trim(job.get("error_summary")),
            ))
        if job_name in {ATTENTION_HOME_JOB, TRADING_AGENT_JOB}:
            continue
        if str(job.get("status") or "").lower() == "failed":
            issues.append(HealthIssue(
                key=f"pipeline-job-failed-{job_name}",
                title=f"Pipeline job {job_name} failed",
                details=_trim(job.get("error_summary") or job.get("progress_message")),
            ))

    for failure in provider_failures:
        job_name = _trim(failure.get("job_name"), 100) or "pipeline"
        details = _trim(failure.get("error_summary") or failure.get("progress_message"))
        issues.append(
            HealthIssue(
                key=f"provider-access-{job_name}",
                title=f"Provider access is blocking {job_name}",
                details=details or "A provider authentication, quota, or billing failure was recorded.",
            )
        )

    return issues


def _latest_jobs(conn: Any) -> dict[str, dict[str, object]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (job_name)
                   job_name, status, start_time_utc, end_time_utc, error_summary, progress_message,
                   progress_stage
            FROM job_runs
            WHERE status NOT IN ('Running', 'Stopped')
              AND CASE WHEN %s = '' THEN position('::' in job_name) = 0
                       ELSE starts_with(job_name, %s) END
            ORDER BY job_name, start_time_utc DESC
            """,
            (dataset_namespace(), f"{dataset_namespace()}::"),
        )
        rows = cur.fetchall() or []
    return {
        str(row[0]).split("::", 1)[-1]: {
            "status": row[1],
            "start_time_utc": row[2],
            "end_time_utc": row[3],
            "error_summary": row[4],
            "progress_message": row[5],
            "progress_stage": row[6],
        }
        for row in rows
    }


def reconcile_job_executions(conn: Any, *, now: datetime) -> int:
    """Finalize orphaned run rows from their exact Azure execution, never their age."""
    resource_group_id = (os.getenv("SYSTEM_HEALTH_AZURE_RESOURCE_GROUP_ID") or "").strip().rstrip("/")
    if not resource_group_id:
        return 0
    if not resource_group_id.startswith("/subscriptions/") or "/resourceGroups/" not in resource_group_id:
        raise SystemHealthConfigurationError("Invalid system health Azure resource group ID.")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT platform_job_name, platform_execution_name
            FROM job_runs
            WHERE status = 'Running' AND platform_job_name IS NOT NULL
              AND platform_execution_name IS NOT NULL
              AND CASE WHEN %s = '' THEN position('::' in job_name) = 0
                       ELSE starts_with(job_name, %s) END
            """, (dataset_namespace(), f"{dataset_namespace()}::"),
        )
        executions = cur.fetchall() or []
    conn.commit()
    if not executions:
        return 0
    credential = build_azure_credential()
    if credential is None:
        raise SystemHealthConfigurationError("Azure job monitoring requires a credential.")
    token = credential.get_token("https://management.azure.com/.default").token
    updated = 0
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=0.5, backoff_max=3,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}), respect_retry_after_header=False,
        )))
        session.headers["Authorization"] = f"Bearer {token}"
        for job_name, execution_name in executions:
            url = (f"https://management.azure.com{resource_group_id}/providers/Microsoft.App/jobs/"
                   f"{quote(job_name, safe='')}/executions/{quote(execution_name, safe='')}")
            response = session.get(url, params={"api-version": "2025-07-01"}, timeout=15)
            if response.status_code != 200:
                raise RuntimeError(f"Azure execution lookup for {job_name} returned HTTP {response.status_code}.")
            props = response.json().get("properties") or {}
            platform_status = str(props.get("status") or "")
            if platform_status not in {"Failed", "Succeeded", "Stopped"}:
                continue
            status = "Warning" if platform_status == "Succeeded" else platform_status
            stage = "platform_completion_unrecorded" if platform_status == "Succeeded" else "platform_terminated"
            detail = (f"Azure execution {execution_name} ended with status {platform_status}; "
                      "the process did not finalize its database run.")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE job_runs SET status = %s, end_time_utc = %s,
                        error_summary = %s, progress_stage = %s, progress_message = %s
                    WHERE status = 'Running' AND platform_job_name = %s
                      AND platform_execution_name = %s
                      AND CASE WHEN %s = '' THEN position('::' in job_name) = 0
                               ELSE starts_with(job_name, %s) END
                    """,
                    (status, _as_utc(props.get("endTime")) or now, detail, stage, detail,
                     job_name, execution_name, dataset_namespace(), f"{dataset_namespace()}::"),
                )
                updated += cur.rowcount
            conn.commit()
    return updated


def _recent_provider_failures(conn: Any, *, since: datetime) -> list[dict[str, object]]:
    patterns = [
        "%insufficient funds%", "%insufficient balance%", "%http 402%", "%quota%", "%rate limit%",
        "%authentication%", "%unauthorized%", "%forbidden%",
    ]
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH completed AS (
                SELECT DISTINCT ON (job_name)
                       job_name, status, start_time_utc, error_summary, progress_message
                FROM job_runs
                WHERE status NOT IN ('Running', 'Stopped')
                  AND CASE WHEN %s = '' THEN position('::' in job_name) = 0
                           ELSE starts_with(job_name, %s) END
                ORDER BY job_name, start_time_utc DESC
            )
            SELECT job_name, start_time_utc, error_summary, progress_message
            FROM completed
            WHERE status = 'Failed' AND start_time_utc >= %s
              AND lower(coalesce(error_summary, '') || ' ' || coalesce(progress_message, '')) LIKE ANY(%s)
            """,
            (dataset_namespace(), f"{dataset_namespace()}::", since, patterns),
        )
        rows = cur.fetchall() or []
    return [
        {"job_name": row[0], "start_time_utc": row[1], "error_summary": row[2], "progress_message": row[3]}
        for row in rows
    ]


def _ensure_incident_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS platform_job_name TEXT")
        cur.execute("ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS platform_execution_name TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS system_health_incidents (
                incident_key TEXT PRIMARY KEY,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at_utc TIMESTAMPTZ NOT NULL,
                last_detected_at_utc TIMESTAMPTZ NOT NULL,
                resolved_at_utc TIMESTAMPTZ,
                opened_notified_at_utc TIMESTAMPTZ,
                recovery_notified_at_utc TIMESTAMPTZ
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_system_health_incidents_open
            ON system_health_incidents (status, last_detected_at_utc DESC)
            """
        )
    conn.commit()


def _open_incidents(conn: Any) -> dict[str, dict[str, object]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident_key, opened_notified_at_utc FROM system_health_incidents WHERE status = 'open'"
        )
        rows = cur.fetchall() or []
    return {str(row[0]): {"opened_notified_at_utc": row[1]} for row in rows}


def _pending_recoveries(conn: Any) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT incident_key, title
            FROM system_health_incidents
            WHERE status = 'resolved' AND recovery_notified_at_utc IS NULL
            ORDER BY resolved_at_utc
            """
        )
        rows = cur.fetchall() or []
    return [(str(row[0]), _trim(row[1], 160) or str(row[0])) for row in rows]


def _apply_incident_state(
    conn: Any, *, issues: list[HealthIssue], now: datetime
) -> tuple[list[HealthIssue], list[str]]:
    existing_open = _open_incidents(conn)
    issue_by_key = {issue.key: issue for issue in issues}
    newly_opened: list[HealthIssue] = []
    recovered: list[str] = []

    with conn.cursor() as cur:
        for issue in issues:
            previous = existing_open.get(issue.key)
            if previous is None or previous.get("opened_notified_at_utc") is None:
                newly_opened.append(issue)
            cur.execute(
                """
                INSERT INTO system_health_incidents (
                    incident_key, severity, title, details, status, opened_at_utc, last_detected_at_utc,
                    resolved_at_utc, opened_notified_at_utc, recovery_notified_at_utc
                ) VALUES (%s, %s, %s, %s, 'open', %s, %s, NULL, NULL, NULL)
                ON CONFLICT (incident_key) DO UPDATE SET
                    severity = EXCLUDED.severity, title = EXCLUDED.title, details = EXCLUDED.details,
                    status = 'open', last_detected_at_utc = EXCLUDED.last_detected_at_utc,
                    resolved_at_utc = NULL, recovery_notified_at_utc = NULL
                """,
                (issue.key, issue.severity, issue.title, issue.details, now, now),
            )
        for issue_key in existing_open:
            if issue_key not in issue_by_key:
                cur.execute(
                    """
                    UPDATE system_health_incidents
                    SET status = 'resolved', resolved_at_utc = %s, last_detected_at_utc = %s
                    WHERE incident_key = %s AND status = 'open'
                    """,
                    (now, now, issue_key),
                )
                recovered.append(issue_key)
    conn.commit()
    return newly_opened, recovered


def _mark_notification_sent(conn: Any, *, issue_keys: list[str], recovery: bool, now: datetime) -> None:
    if not issue_keys:
        return
    column = "recovery_notified_at_utc" if recovery else "opened_notified_at_utc"
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE system_health_incidents SET {column} = %s WHERE incident_key = ANY(%s)",
            (now, issue_keys),
        )
    conn.commit()


def _alert_recipient() -> str:
    recipient = resolve_secret_value(
        ["SYSTEM_HEALTH_ALERT_EMAIL"],
        secret_name_env="SYSTEM_HEALTH_ALERT_EMAIL_SECRET",
        default_secret_name="dashboard-bootstrap-admin-email",
    ).strip()
    if not recipient:
        raise SystemHealthConfigurationError(
            "System health alert recipient is unavailable. Configure SYSTEM_HEALTH_ALERT_EMAIL "
            "or SYSTEM_HEALTH_ALERT_EMAIL_SECRET."
        )
    return recipient


def _issue_email_body(issues: list[HealthIssue], *, now: datetime) -> str:
    lines = ["Spectral Nature needs attention.", "", f"Observed at {now.isoformat()}.", ""]
    for issue in issues:
        lines.extend((issue.title, issue.details, ""))
    lines.append("Open Admin > System Health for current pipeline status.")
    return "\n".join(lines)


def _recovery_email_body(issues: list[tuple[str, str]], *, now: datetime) -> str:
    return "\n".join(
        [
            "Spectral Nature recovered from the following health incidents.",
            "",
            f"Observed at {now.isoformat()}.",
            *[f"- {title}" for _key, title in issues],
        ]
    )


def run_system_health_check(
    *,
    conn: Any,
    now: datetime | None = None,
    policy: SystemHealthPolicy | None = None,
    send_fn: Callable[..., EmailDeliveryResult] = send_email,
) -> SystemHealthResult:
    """Persist health transitions and notify on open/recovery only."""
    if conn is None:
        raise RuntimeError("System health monitoring requires a database connection.")
    observed_at = (now or _utc_now()).astimezone(timezone.utc)
    active_policy = policy or policy_from_environment()
    _ensure_incident_table(conn)

    platform_issues: list[HealthIssue] = []
    try:
        reconcile_job_executions(conn, now=observed_at)
    except Exception as exc:
        conn.rollback()
        platform_issues.append(HealthIssue(
            key="job-execution-monitor-unavailable",
            title="Platform job monitoring failed",
            details=_trim(f"{type(exc).__name__}: {exc}"),
        ))

    latest_jobs = _latest_jobs(conn)
    conn.commit()
    try:
        home_metadata = latest_dataset_metadata(ATTENTION_HOME_DATASET)
    except Exception:
        home_metadata = None
    try:
        trading_frame, _ = load_latest_dataset_frame(TRADING_AGENT_RUNS_DATASET)
    except Exception:
        trading_frame = pd.DataFrame()
    provider_failures = _recent_provider_failures(
        conn, since=observed_at - timedelta(hours=active_policy.provider_failure_window_hours)
    )
    issues = evaluate_system_health(
        now=observed_at,
        policy=active_policy,
        latest_jobs=latest_jobs,
        home_metadata=home_metadata,
        trading_successful_horizons=_successful_horizon_count(trading_frame),
        provider_failures=provider_failures,
    )
    issues.extend(platform_issues)
    opened, newly_recovered = _apply_incident_state(conn, issues=issues, now=observed_at)
    pending_recoveries = _pending_recoveries(conn)

    emails_sent = 0
    if opened:
        result = send_fn(
            to_address=_alert_recipient(),
            subject="Spectral Nature: action required",
            text_body=_issue_email_body(opened, now=observed_at),
        )
        if not result.sent:
            raise RuntimeError(result.message)
        _mark_notification_sent(conn, issue_keys=[issue.key for issue in opened], recovery=False, now=observed_at)
        emails_sent += 1
    if pending_recoveries:
        result = send_fn(
            to_address=_alert_recipient(),
            subject="Spectral Nature: system health recovered",
            text_body=_recovery_email_body(pending_recoveries, now=observed_at),
        )
        if not result.sent:
            raise RuntimeError(result.message)
        _mark_notification_sent(
            conn,
            issue_keys=[issue_key for issue_key, _title in pending_recoveries],
            recovery=True,
            now=observed_at,
        )
        emails_sent += 1

    return SystemHealthResult(
        open_issues=tuple(issues),
        opened_issue_keys=tuple(issue.key for issue in opened),
        recovered_issue_keys=tuple(newly_recovered),
        emails_sent=emails_sent,
    )


__all__ = [
    "HealthIssue", "SystemHealthConfigurationError", "SystemHealthPolicy", "SystemHealthResult",
    "evaluate_system_health", "policy_from_environment", "run_system_health_check",
]
