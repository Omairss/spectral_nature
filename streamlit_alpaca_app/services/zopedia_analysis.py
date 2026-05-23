from __future__ import annotations

import ast
import builtins
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import multiprocessing as mp
import queue
import re
import time
import traceback
from typing import Any
import uuid

import numpy as np
import pandas as pd

from data_access.contracts import frame_to_records, to_jsonable
from data_access.query_service import QueryService
from services.saa.storage import _db_connection


try:
    import resource
except Exception:  # pragma: no cover - resource is POSIX-only.
    resource = None


MAX_DATASET_ROWS = 10_000
MAX_DATASET_COLUMNS = 80
MAX_CELL_CHARS = 2_000
MAX_TABLE_ROWS = 200
MAX_ARTIFACTS = 24
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MEMORY_MB = 2048
DEFAULT_RAW_OUTPUT_MAX_CHARS = 2_000
MAX_RAW_OUTPUT_MAX_CHARS = 12_000

ANALYSIS_RUN_TABLE = "saa_zopedia_analysis_runs"
ANALYSIS_ARTIFACT_TABLE = "saa_zopedia_analysis_artifacts"

_ALLOWED_IMPORT_ROOTS = {
    "json",
    "math",
    "numpy",
    "pandas",
    "scipy",
    "sklearn",
    "statistics",
}
_ALLOWED_IMPORT_ALIASES = {
    "np": "numpy",
    "pd": "pandas",
}
_BLOCKED_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "vars",
}
_BLOCKED_MODULE_ROOTS = {
    "builtins",
    "ctypes",
    "http",
    "importlib",
    "joblib",
    "multiprocessing",
    "os",
    "pathlib",
    "pickle",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "urllib",
}
_BLOCKED_ATTRS = {
    "chmod",
    "chown",
    "connect",
    "dump",
    "dumps",
    "fork",
    "from_pickle",
    "fromfile",
    "getenv",
    "listdir",
    "load",
    "loads",
    "makedirs",
    "mkdir",
    "popen",
    "read_clipboard",
    "read_csv",
    "read_excel",
    "read_feather",
    "read_fwf",
    "read_gbq",
    "read_hdf",
    "read_html",
    "read_json",
    "read_orc",
    "read_parquet",
    "read_pickle",
    "read_sas",
    "read_spss",
    "read_sql",
    "read_sql_query",
    "read_sql_table",
    "read_stata",
    "read_table",
    "read_xml",
    "remove",
    "request",
    "rmdir",
    "run",
    "spawn",
    "system",
    "to_clipboard",
    "to_csv",
    "to_excel",
    "to_feather",
    "to_gbq",
    "to_hdf",
    "to_html",
    "to_json",
    "to_latex",
    "to_orc",
    "to_parquet",
    "to_pickle",
    "to_sql",
    "to_stata",
    "to_xml",
    "unlink",
    "urlopen",
    "walk",
}


class AnalysisRejectedError(ValueError):
    pass


_SINGLE_LINE_BLOCK_STATEMENTS = ("break", "continue", "pass", "raise", "return")


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        text = str(value).strip()
    return "" if text.upper() in {"NAN", "<NA>", "NONE", "NULL"} else text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: object) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=True, sort_keys=True, default=str)


def _sha256_text(value: object) -> str:
    return hashlib.sha256(_coerce_text(value).encode("utf-8")).hexdigest()


def _slug(value: object, *, default: str = "dataset") -> str:
    text = re.sub(r"[^a-z0-9_]+", "_", _coerce_text(value).lower()).strip("_")
    if not text:
        text = default
    if text[0].isdigit():
        text = f"{default}_{text}"
    return text[:80]


def _normalize_numeric(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min(parsed, maximum), minimum)


def _truncate_cell(value: object) -> object:
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[: MAX_CELL_CHARS - 3].rstrip() + "..."
    return value


def _normalize_frame(frame: pd.DataFrame, *, max_rows: int = MAX_DATASET_ROWS) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    frame = frame.copy()
    if len(frame.columns) > MAX_DATASET_COLUMNS:
        frame = frame.iloc[:, :MAX_DATASET_COLUMNS]
    if len(frame) > max_rows:
        frame = frame.tail(max_rows)
    frame.columns = [str(column) for column in frame.columns]
    object_columns = list(frame.select_dtypes(include=["object", "string"]).columns)
    for column in object_columns:
        frame[column] = frame[column].map(_truncate_cell)
    return frame.reset_index(drop=True)


def _payload_to_frames(payload: object, *, alias: str, max_rows: int) -> dict[str, pd.DataFrame]:
    if isinstance(payload, pd.DataFrame):
        return {alias: _normalize_frame(payload, max_rows=max_rows)}
    if isinstance(payload, list):
        return {alias: _normalize_frame(pd.DataFrame(payload), max_rows=max_rows)}
    if isinstance(payload, dict):
        frames: dict[str, pd.DataFrame] = {}
        scalar_row: dict[str, object] = {}
        for key, value in payload.items():
            name = f"{alias}_{_slug(key)}"
            if isinstance(value, pd.DataFrame):
                frames[name] = _normalize_frame(value, max_rows=max_rows)
            elif isinstance(value, list) and all(isinstance(item, dict) for item in value):
                frames[name] = _normalize_frame(pd.DataFrame(value), max_rows=max_rows)
            elif isinstance(value, dict):
                frames[name] = _normalize_frame(pd.DataFrame([value]), max_rows=max_rows)
            else:
                scalar_row[str(key)] = value
        if scalar_row:
            frames[alias] = _normalize_frame(pd.DataFrame([scalar_row]), max_rows=max_rows)
        return frames
    return {alias: _normalize_frame(pd.DataFrame([{"value": payload}]), max_rows=max_rows)}


def _dataset_ref_alias(ref: dict[str, Any], *, dataset_name: str, idx: int, used_aliases: set[str]) -> str:
    explicit_alias = _coerce_text(ref.get("alias"))
    params = ref.get("params") if isinstance(ref.get("params"), dict) else {}
    base = _slug(explicit_alias or dataset_name)
    if not explicit_alias and base in used_aliases and params:
        parts = [dataset_name]
        for key, value in sorted(params.items(), key=lambda item: str(item[0])):
            if isinstance(value, (str, int, float, bool)) and _coerce_text(value):
                parts.append(f"{key}_{value}")
        base = _slug("_".join(parts), default=dataset_name)
    if base not in used_aliases:
        used_aliases.add(base)
        return base
    suffix = idx + 1
    while True:
        candidate = _slug(f"{base}_{suffix}", default=dataset_name)
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            return candidate
        suffix += 1


def _frame_payload(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return frame_to_records(_normalize_frame(frame))


def _artifact_id(run_id: str, name: str, idx: int) -> str:
    return f"{run_id}::artifact::{idx:02d}::{_sha256_text(name)[:10]}"


def bootstrap_zopedia_analysis_storage(conn: Any, *, commit: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_analysis_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                objective TEXT,
                code_hash TEXT,
                code_text TEXT,
                input_refs_json JSONB,
                output_summary_json JSONB,
                stdout_text TEXT,
                stderr_text TEXT,
                traceback_text TEXT,
                error_text TEXT,
                duration_ms INTEGER,
                created_at_utc TIMESTAMPTZ NOT NULL,
                metadata_json JSONB
            )
            """
        )
        cur.execute("ALTER TABLE saa_zopedia_analysis_runs ADD COLUMN IF NOT EXISTS code_text TEXT")
        cur.execute("ALTER TABLE saa_zopedia_analysis_runs ADD COLUMN IF NOT EXISTS traceback_text TEXT")
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_analysis_runs_created
            ON saa_zopedia_analysis_runs (created_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS saa_zopedia_analysis_artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                name TEXT NOT NULL,
                payload_json JSONB,
                preview_text TEXT,
                created_at_utc TIMESTAMPTZ NOT NULL,
                metadata_json JSONB
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_saa_zopedia_analysis_artifacts_run
            ON saa_zopedia_analysis_artifacts (run_id, created_at_utc DESC)
            """
        )
    if commit:
        conn.commit()


def _persist_analysis_result(conn: Any, payload: dict[str, Any]) -> None:
    bootstrap_zopedia_analysis_storage(conn, commit=False)
    run_id = str(payload.get("analysis_run_id") or "")
    created_at = payload.get("created_at_utc") or _utc_now()
    output_summary = {
        "metrics": payload.get("metrics") or [],
        "tables": [
            {key: value for key, value in dict(table).items() if key != "rows"}
            for table in list(payload.get("tables") or [])
            if isinstance(table, dict)
        ],
        "charts": payload.get("charts") or [],
        "artifact_count": len(list(payload.get("artifacts") or [])),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO saa_zopedia_analysis_runs (
                run_id, status, objective, code_hash, code_text, input_refs_json, output_summary_json,
                stdout_text, stderr_text, traceback_text, error_text, duration_ms, created_at_utc, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                objective = EXCLUDED.objective,
                code_hash = EXCLUDED.code_hash,
                code_text = EXCLUDED.code_text,
                input_refs_json = EXCLUDED.input_refs_json,
                output_summary_json = EXCLUDED.output_summary_json,
                stdout_text = EXCLUDED.stdout_text,
                stderr_text = EXCLUDED.stderr_text,
                traceback_text = EXCLUDED.traceback_text,
                error_text = EXCLUDED.error_text,
                duration_ms = EXCLUDED.duration_ms,
                metadata_json = EXCLUDED.metadata_json
            """,
            (
                run_id,
                str(payload.get("status") or "unknown"),
                str(payload.get("objective") or ""),
                str(payload.get("code_hash") or ""),
                str(payload.get("code_text") or ""),
                _json_dumps(payload.get("input_refs") or []),
                _json_dumps(output_summary),
                str(payload.get("stdout") or ""),
                str(payload.get("stderr") or ""),
                str(payload.get("traceback") or ""),
                str(payload.get("error") or ""),
                int(payload.get("duration_ms") or 0),
                created_at,
                _json_dumps(payload.get("metadata") or {}),
            ),
        )
        artifact_records = []
        for artifact in list(payload.get("artifacts") or [])[:MAX_ARTIFACTS]:
            if not isinstance(artifact, dict):
                continue
            artifact_records.append(
                (
                    str(artifact.get("artifact_id") or ""),
                    run_id,
                    str(artifact.get("artifact_type") or "artifact"),
                    str(artifact.get("name") or "artifact"),
                    _json_dumps(artifact.get("payload") or {}),
                    str(artifact.get("preview_text") or ""),
                    created_at,
                    _json_dumps(artifact.get("metadata") or {}),
                )
            )
        if artifact_records:
            cur.executemany(
                """
                INSERT INTO saa_zopedia_analysis_artifacts (
                    artifact_id, run_id, artifact_type, name, payload_json,
                    preview_text, created_at_utc, metadata_json
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE SET
                    artifact_type = EXCLUDED.artifact_type,
                    name = EXCLUDED.name,
                    payload_json = EXCLUDED.payload_json,
                    preview_text = EXCLUDED.preview_text,
                    metadata_json = EXCLUDED.metadata_json
                """,
                artifact_records,
            )
    conn.commit()


def resolve_analysis_input_frames(
    *,
    service: QueryService,
    dataset_refs: list[dict[str, Any]] | None = None,
    inline_datasets: list[dict[str, Any]] | None = None,
    max_rows: int = MAX_DATASET_ROWS,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]], list[str]]:
    frames: dict[str, pd.DataFrame] = {}
    input_refs: list[dict[str, Any]] = []
    messages: list[str] = []
    used_aliases: set[str] = set()

    for idx, ref in enumerate(list(dataset_refs or [])):
        if not isinstance(ref, dict):
            messages.append(f"Skipped dataset_ref[{idx}]: expected object.")
            continue
        dataset_name = _coerce_text(ref.get("name") or ref.get("dataset") or ref.get("dataset_name"))
        if not dataset_name:
            messages.append(f"Skipped dataset_ref[{idx}]: missing dataset name.")
            continue
        params = ref.get("params") if isinstance(ref.get("params"), dict) else {}
        alias = _dataset_ref_alias(ref, dataset_name=dataset_name, idx=idx, used_aliases=used_aliases)
        resolved = service.fetch_dataset(dataset_name, params)
        new_frames = _payload_to_frames(resolved.payload, alias=alias, max_rows=max_rows)
        frames.update(new_frames)
        input_refs.append(
            {
                "kind": "query_service_dataset",
                "dataset": dataset_name,
                "alias": alias,
                "params": to_jsonable(params),
                "frame_names": sorted(new_frames),
                "provenance": resolved.provenance.to_dict() if resolved.provenance is not None else None,
            }
        )

    for idx, item in enumerate(list(inline_datasets or [])):
        if not isinstance(item, dict):
            messages.append(f"Skipped inline_datasets[{idx}]: expected object.")
            continue
        alias = _slug(item.get("name") or item.get("alias") or f"inline_{idx + 1}")
        rows = item.get("rows")
        if rows is None:
            rows = item.get("records")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            messages.append(f"Skipped inline dataset '{alias}': rows must be a list of objects.")
            continue
        frame = _normalize_frame(pd.DataFrame(rows), max_rows=max_rows)
        frames[alias] = frame
        input_refs.append(
            {
                "kind": "inline_dataset",
                "alias": alias,
                "rows": len(frame),
                "columns": list(frame.columns),
            }
        )

    return frames, input_refs, messages


def build_analysis_input_profile(
    *,
    service: QueryService,
    dataset_refs: list[dict[str, Any]] | None = None,
    inline_datasets: list[dict[str, Any]] | None = None,
    max_rows: int = MAX_DATASET_ROWS,
    sample_rows: int = 3,
) -> dict[str, Any]:
    """Return a compact profile of the frames available to analysis code."""
    try:
        frames, input_refs, messages = resolve_analysis_input_frames(
            service=service,
            dataset_refs=dataset_refs,
            inline_datasets=inline_datasets,
            max_rows=_normalize_numeric(max_rows, default=MAX_DATASET_ROWS, minimum=1, maximum=MAX_DATASET_ROWS),
        )
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "frames": [],
            "input_refs": [],
            "messages": [],
        }

    row_limit = _normalize_numeric(sample_rows, default=3, minimum=1, maximum=8)
    frame_profiles: list[dict[str, Any]] = []
    for alias, frame in sorted(frames.items()):
        normalized = _normalize_frame(frame, max_rows=max_rows)
        frame_profiles.append(
            {
                "alias": alias,
                "rows": int(len(normalized)),
                "columns": list(normalized.columns),
                "dtypes": {str(column): str(dtype) for column, dtype in normalized.dtypes.items()},
                "sample": frame_to_records(normalized.head(row_limit)),
            }
        )

    return {
        "status": "ok",
        "frames": frame_profiles,
        "input_refs": input_refs,
        "messages": messages,
    }


def _module_root(name: str) -> str:
    return str(name or "").split(".", 1)[0]


def _module_is_allowed(name: str) -> bool:
    root = _module_root(name)
    return root in _ALLOWED_IMPORT_ROOTS and root not in _BLOCKED_MODULE_ROOTS


def _line_indent(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _assigned_name(line: str) -> str:
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", line.strip())
    return match.group(1) if match else ""


def _for_target_names(header: str) -> set[str]:
    match = re.match(r"for\s+(.+?)\s+in\s+.+:", header.strip())
    if not match:
        return set()
    target = match.group(1)
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", target))


def _references_any_name(line: str, names: set[str]) -> bool:
    if not names:
        return False
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line))
    return bool(tokens.intersection(names))


def _indent_generated_block_lines(lines: list[str], idx: int) -> bool:
    header = lines[idx]
    stripped_header = header.strip()
    parent_indent = _line_indent(header)
    child_indent = parent_indent + 4
    changed = False

    if stripped_header.startswith("for "):
        block_names = _for_target_names(stripped_header)
        for next_idx in range(idx + 1, len(lines)):
            next_line = lines[next_idx]
            next_stripped = next_line.strip()
            if not next_stripped:
                continue
            next_indent = _line_indent(next_line)
            if next_indent > parent_indent:
                assigned = _assigned_name(next_stripped)
                if assigned:
                    block_names.add(assigned)
                continue
            if next_stripped.startswith(_SINGLE_LINE_BLOCK_STATEMENTS):
                lines[next_idx] = " " * child_indent + next_stripped
                changed = True
                continue
            if not _references_any_name(next_stripped, block_names):
                break
            lines[next_idx] = " " * child_indent + next_stripped
            changed = True
            assigned = _assigned_name(next_stripped)
            if assigned:
                block_names.add(assigned)
        return changed

    for next_idx in range(idx + 1, len(lines)):
        next_line = lines[next_idx]
        next_stripped = next_line.strip()
        if not next_stripped:
            continue
        if _line_indent(next_line) > parent_indent:
            break
        if next_stripped.startswith(_SINGLE_LINE_BLOCK_STATEMENTS):
            lines[next_idx] = " " * child_indent + next_stripped
            changed = True
        break
    return changed


def normalize_analysis_code(code: str) -> str:
    """Normalize narrow formatting errors common in generated Python code."""
    clean = _coerce_text(code)
    if not clean:
        return ""
    try:
        ast.parse(clean, mode="exec")
        return clean
    except SyntaxError as exc:
        message = str(exc).lower()
        if "expected an indented block" not in message:
            return clean

    candidate = clean
    for _ in range(6):
        lines = candidate.splitlines()
        changed = False
        for idx, line in enumerate(lines[:-1]):
            stripped = line.strip()
            if stripped.endswith(":"):
                changed = _indent_generated_block_lines(lines, idx) or changed
        if not changed:
            return clean
        candidate = "\n".join(lines)
        try:
            ast.parse(candidate, mode="exec")
            return candidate
        except SyntaxError as exc:
            if "expected an indented block" not in str(exc).lower():
                return clean
    return clean


def validate_analysis_code(code: str) -> None:
    clean = normalize_analysis_code(code)
    if not clean:
        raise AnalysisRejectedError("Analysis code is empty.")
    try:
        tree = ast.parse(clean, mode="exec")
    except SyntaxError as exc:
        raise AnalysisRejectedError(f"Syntax error: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            raise AnalysisRejectedError("Class definitions are not allowed in analysis code.")
        if isinstance(node, ast.Name):
            if node.id in _BLOCKED_NAMES or node.id.startswith("__"):
                raise AnalysisRejectedError(f"Blocked name '{node.id}' in analysis code.")
        if isinstance(node, ast.Attribute):
            if node.attr in _BLOCKED_ATTRS or node.attr.startswith("__"):
                raise AnalysisRejectedError(f"Blocked attribute '{node.attr}' in analysis code.")
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = _ALLOWED_IMPORT_ALIASES.get(alias.name, alias.name)
                if not _module_is_allowed(module_name):
                    raise AnalysisRejectedError(f"Import '{alias.name}' is not allowed.")
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise AnalysisRejectedError("Relative imports are not allowed.")
            if not _module_is_allowed(node.module or ""):
                raise AnalysisRejectedError(f"Import from '{node.module}' is not allowed.")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BLOCKED_NAMES:
                raise AnalysisRejectedError(f"Call to '{func.id}' is not allowed.")
            if isinstance(func, ast.Attribute) and (func.attr in _BLOCKED_ATTRS or func.attr.startswith("__")):
                raise AnalysisRejectedError(f"Call to '{func.attr}' is not allowed.")


def _safe_import(name: str, globals_: object = None, locals_: object = None, fromlist: tuple[str, ...] = (), level: int = 0) -> Any:
    if level:
        raise ImportError("Relative imports are not allowed.")
    module_name = _ALLOWED_IMPORT_ALIASES.get(name, name)
    if not _module_is_allowed(module_name):
        raise ImportError(f"Import '{name}' is not allowed.")
    return builtins.__import__(module_name, globals_, locals_, fromlist, level)


def _safe_builtins() -> dict[str, Any]:
    allowed_names = [
        "ArithmeticError",
        "AssertionError",
        "Exception",
        "FloatingPointError",
        "IndexError",
        "KeyError",
        "RuntimeError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "filter",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "map",
        "max",
        "min",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "type",
        "zip",
    ]
    safe = {name: getattr(builtins, name) for name in allowed_names}
    safe["__import__"] = _safe_import
    return safe


def _jsonable_preview(value: object, *, max_rows: int = MAX_TABLE_ROWS) -> object:
    if isinstance(value, pd.DataFrame):
        return frame_to_records(_normalize_frame(value, max_rows=max_rows).head(max_rows))
    if isinstance(value, pd.Series):
        return to_jsonable(value.head(max_rows).tolist())
    if isinstance(value, np.generic):
        return to_jsonable(value.item())
    if isinstance(value, np.ndarray):
        return to_jsonable(value[:max_rows].tolist())
    return to_jsonable(value)


def _worker_artifact(
    *,
    artifacts: list[dict[str, Any]],
    artifact_type: str,
    name: str,
    payload: object,
    preview_text: str = "",
) -> None:
    if len(artifacts) >= MAX_ARTIFACTS:
        return
    artifacts.append(
        {
            "artifact_type": _coerce_text(artifact_type) or "artifact",
            "name": _coerce_text(name) or "artifact",
            "payload": _jsonable_preview(payload),
            "preview_text": _coerce_text(preview_text),
            "metadata": {},
        }
    )


def _analysis_worker(
    code: str,
    frame_records: dict[str, list[dict[str, Any]]],
    result_queue: Any,
    memory_mb: int,
) -> None:
    if resource is not None:
        try:
            memory_bytes = max(int(memory_mb), 128) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except Exception:
            pass

    datasets = {name: pd.DataFrame(rows) for name, rows in dict(frame_records or {}).items()}
    artifacts: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}

    def add_metric(name: str, value: object, *, description: str = "") -> None:
        metrics.append(
            {
                "name": _coerce_text(name) or f"metric_{len(metrics) + 1}",
                "value": _jsonable_preview(value),
                "description": _coerce_text(description),
            }
        )

    def add_table(name: str, frame_or_records: object, *, max_rows: int = MAX_TABLE_ROWS) -> None:
        row_limit = _normalize_numeric(max_rows, default=MAX_TABLE_ROWS, minimum=1, maximum=MAX_TABLE_ROWS)
        frame = _normalize_frame(pd.DataFrame(frame_or_records), max_rows=row_limit)
        rows = frame_to_records(frame.head(row_limit))
        table = {
            "name": _coerce_text(name) or f"table_{len(tables) + 1}",
            "rows": rows,
            "row_count": int(len(frame)),
            "columns": list(frame.columns),
        }
        tables.append(table)
        _worker_artifact(
            artifacts=artifacts,
            artifact_type="table",
            name=str(table["name"]),
            payload={"rows": rows, "columns": list(frame.columns), "row_count": int(len(frame))},
            preview_text=f"{table['name']}: {len(rows)} preview rows",
        )

    def add_chart(name: str, *, kind: str, x: object, y: object, series_name: str = "") -> None:
        x_values = _jsonable_preview(list(x) if not isinstance(x, list) else x)
        y_values = _jsonable_preview(list(y) if not isinstance(y, list) else y)
        chart = {
            "name": _coerce_text(name) or f"chart_{len(charts) + 1}",
            "kind": _coerce_text(kind) or "line",
            "x": x_values,
            "y": y_values,
            "series_name": _coerce_text(series_name),
        }
        charts.append(chart)
        _worker_artifact(
            artifacts=artifacts,
            artifact_type="chart",
            name=str(chart["name"]),
            payload=chart,
            preview_text=f"{chart['name']} ({chart['kind']})",
        )

    def add_artifact(name: str, payload: object, *, artifact_type: str = "artifact", preview_text: str = "") -> None:
        _worker_artifact(
            artifacts=artifacts,
            artifact_type=artifact_type,
            name=name,
            payload=payload,
            preview_text=preview_text,
        )

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    globals_dict: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "add_artifact": add_artifact,
        "add_chart": add_chart,
        "add_metric": add_metric,
        "add_table": add_table,
        "datasets": datasets,
        "np": np,
        "pd": pd,
        "result": result,
    }
    globals_dict.update(datasets)

    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(compile(code, "<zopedia-analysis>", "exec"), globals_dict, globals_dict)
        if isinstance(globals_dict.get("result"), dict):
            result = to_jsonable(globals_dict.get("result") or {})
        result_queue.put(
            {
                "status": "succeeded",
                "stdout": stdout_buffer.getvalue()[-8000:],
                "stderr": stderr_buffer.getvalue()[-8000:],
                "result": result,
                "metrics": metrics,
                "tables": tables[:6],
                "charts": charts[:6],
                "artifacts": artifacts[:MAX_ARTIFACTS],
            }
        )
    except BaseException as exc:  # noqa: BLE001 - returned to parent as bounded tool output.
        result_queue.put(
            {
                "status": "failed",
                "stdout": stdout_buffer.getvalue()[-8000:],
                "stderr": stderr_buffer.getvalue()[-8000:],
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=8),
                "result": {},
                "metrics": metrics,
                "tables": tables[:6],
                "charts": charts[:6],
                "artifacts": artifacts[:MAX_ARTIFACTS],
            }
        )


def _text_stats(text: object) -> dict[str, Any]:
    value = str(text or "")
    lines = value.splitlines()
    return {
        "char_count": len(value),
        "line_count": len(lines),
        "nonempty_line_count": sum(1 for line in lines if line.strip()),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest() if value else "",
    }


def _format_text_stats(label: str, text: object) -> str:
    stats = _text_stats(text)
    if not stats["char_count"]:
        return ""
    digest = str(stats.get("sha256") or "")[:12]
    return (
        f"{label}: {stats['line_count']} line(s), {stats['char_count']} char(s), "
        f"sha256={digest}. Raw text is available through analysis.read_raw_output."
    )


def _summarize_result_payload(result_payload: object) -> str:
    if not isinstance(result_payload, dict) or not result_payload:
        return ""
    scalar_parts: list[str] = []
    collection_parts: list[str] = []
    for key, value in result_payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            text = _coerce_text(value)
            if text:
                scalar_parts.append(f"{key}={text[:120]}")
        elif isinstance(value, (list, tuple, set)):
            collection_parts.append(f"{key}: {len(value)} item(s)")
        elif isinstance(value, dict):
            collection_parts.append(f"{key}: object with {len(value)} key(s)")
        else:
            collection_parts.append(f"{key}: {type(value).__name__}")
    parts = scalar_parts[:10] + collection_parts[:10]
    return "Result summary: " + "; ".join(parts) if parts else ""


def _build_llm_context(payload: dict[str, Any]) -> str:
    lines = [
        f"Zopedia analysis run {payload.get('analysis_run_id')} {payload.get('status')}.",
        f"Objective: {payload.get('objective') or 'not specified'}",
    ]
    input_refs = list(payload.get("input_refs") or [])
    if input_refs:
        lines.append("Inputs:")
        for ref in input_refs[:8]:
            if not isinstance(ref, dict):
                continue
            lines.append(
                "- "
                + " | ".join(
                    part
                    for part in [
                        str(ref.get("kind") or ""),
                        str(ref.get("dataset") or ref.get("alias") or ""),
                        ", ".join(list(ref.get("frame_names") or [])),
                    ]
                    if part
                )
            )
    metrics = list(payload.get("metrics") or [])
    if metrics:
        lines.append("Metrics:")
        for metric in metrics[:12]:
            if isinstance(metric, dict):
                lines.append(f"- {metric.get('name')}: {metric.get('value')}")
    tables = list(payload.get("tables") or [])
    if tables:
        lines.append("Tables:")
        for table in tables[:4]:
            if isinstance(table, dict):
                lines.append(
                    f"- {table.get('name')}: {table.get('row_count')} rows; columns={', '.join(list(table.get('columns') or [])[:12])}"
                )
    log_stats = [
        _format_text_stats("Stdout", payload.get("stdout")),
        _format_text_stats("Stderr", payload.get("stderr")),
        _format_text_stats("Traceback", payload.get("traceback")),
    ]
    log_stats = [item for item in log_stats if item]
    if log_stats:
        lines.append("Output logs:")
        lines.extend(f"- {item}" for item in log_stats)
    result_payload = payload.get("result")
    result_summary = _summarize_result_payload(result_payload)
    if result_summary:
        lines.append(result_summary)
    if payload.get("error"):
        failure_category = str((payload.get("metadata") or {}).get("failure_category") or "").strip()
        if failure_category:
            lines.append(f"Failure category: {failure_category}")
        lines.append(f"Error: {payload.get('error')}")
    return "\n".join(line for line in lines if line)


def _row_value(row: object, index: int, key: str, columns: list[str]) -> object:
    if row is None:
        return ""
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except Exception:
        pass
    try:
        return getattr(row, key)
    except Exception:
        return ""


def read_analysis_raw_output(
    *,
    analysis_run_id: str,
    stream: str = "stdout",
    max_chars: int = DEFAULT_RAW_OUTPUT_MAX_CHARS,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Read bounded raw output for a persisted analysis run.

    Normal analysis results intentionally return summary-only LLM context. This
    function is the explicit inspection path for stdout/stderr/error/traceback.
    """
    run_id = _coerce_text(analysis_run_id)
    normalized_stream = _coerce_text(stream).lower() or "stdout"
    if normalized_stream not in {"stdout", "stderr", "error", "traceback", "all"}:
        normalized_stream = "stdout"
    char_limit = _normalize_numeric(
        max_chars,
        default=DEFAULT_RAW_OUTPUT_MAX_CHARS,
        minimum=1,
        maximum=MAX_RAW_OUTPUT_MAX_CHARS,
    )
    if not run_id:
        return {
            "status": "failed",
            "analysis_run_id": "",
            "stream": normalized_stream,
            "raw_text": "",
            "returned_chars": 0,
            "total_chars": 0,
            "truncated": False,
            "error": "analysis_run_id is required.",
            "llm_context_text": "Raw analysis output lookup failed: analysis_run_id is required.",
        }

    own_conn = None
    active_conn = conn
    if active_conn is None:
        own_conn = _db_connection()
        active_conn = own_conn
    if active_conn is None:
        return {
            "status": "failed",
            "analysis_run_id": run_id,
            "stream": normalized_stream,
            "raw_text": "",
            "returned_chars": 0,
            "total_chars": 0,
            "truncated": False,
            "error": "Analysis storage is not configured.",
            "llm_context_text": f"Raw analysis output for {run_id} is unavailable because analysis storage is not configured.",
        }

    try:
        bootstrap_zopedia_analysis_storage(active_conn, commit=own_conn is not None)
        columns = [
            "run_id",
            "status",
            "objective",
            "stdout_text",
            "stderr_text",
            "error_text",
            "traceback_text",
            "created_at_utc",
        ]
        with active_conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, status, objective, stdout_text, stderr_text,
                       error_text, traceback_text, created_at_utc
                FROM saa_zopedia_analysis_runs
                WHERE run_id = %s
                LIMIT 1
                """,
                (run_id,),
            )
            row = cur.fetchone()
        if not row:
            return {
                "status": "not_found",
                "analysis_run_id": run_id,
                "stream": normalized_stream,
                "raw_text": "",
                "returned_chars": 0,
                "total_chars": 0,
                "truncated": False,
                "error": "Analysis run not found.",
                "llm_context_text": f"Raw analysis output not found for {run_id}.",
            }
        values = {
            column: _row_value(row, idx, column, columns)
            for idx, column in enumerate(columns)
        }
        streams = {
            "stdout": str(values.get("stdout_text") or ""),
            "stderr": str(values.get("stderr_text") or ""),
            "error": str(values.get("error_text") or ""),
            "traceback": str(values.get("traceback_text") or ""),
        }
        if normalized_stream == "all":
            raw_parts = []
            for label, text in streams.items():
                if text:
                    raw_parts.append(f"[{label}]\n{text}")
            full_text = "\n\n".join(raw_parts)
        else:
            full_text = streams.get(normalized_stream, "")
        raw_text = full_text[-char_limit:] if len(full_text) > char_limit else full_text
        stats = _text_stats(full_text)
        returned_stats = _text_stats(raw_text)
        truncated = len(full_text) > len(raw_text)
        llm_lines = [
            (
                f"Explicit raw {normalized_stream} output for {run_id}: "
                f"{stats['line_count']} line(s), {stats['char_count']} char(s); "
                f"returned {returned_stats['char_count']} char(s)"
                + (" from the tail." if truncated else ".")
            )
        ]
        if raw_text:
            llm_lines.append("Raw excerpt:")
            llm_lines.append(raw_text)
        return {
            "status": "ok",
            "analysis_run_id": run_id,
            "analysis_status": str(values.get("status") or ""),
            "objective": str(values.get("objective") or ""),
            "stream": normalized_stream,
            "raw_text": raw_text,
            "returned_chars": int(returned_stats["char_count"]),
            "total_chars": int(stats["char_count"]),
            "line_count": int(stats["line_count"]),
            "nonempty_line_count": int(stats["nonempty_line_count"]),
            "truncated": truncated,
            "created_at_utc": str(values.get("created_at_utc") or ""),
            "metadata": {"context_policy": "explicit_raw_output"},
            "llm_context_text": "\n".join(llm_lines),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "analysis_run_id": run_id,
            "stream": normalized_stream,
            "raw_text": "",
            "returned_chars": 0,
            "total_chars": 0,
            "truncated": False,
            "error": f"{type(exc).__name__}: {exc}",
            "llm_context_text": f"Raw analysis output lookup failed for {run_id}: {type(exc).__name__}.",
        }
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:
                pass


def _base_result_payload(
    *,
    status: str,
    run_id: str,
    objective: str,
    code: str,
    input_refs: list[dict[str, Any]],
    messages: list[str],
    started_at: float,
    error: str = "",
    failure_category: str = "",
) -> dict[str, Any]:
    payload = {
        "analysis_run_id": run_id,
        "status": status,
        "objective": objective,
        "code_hash": _sha256_text(code),
        "code_text": code,
        "input_refs": input_refs,
        "messages": messages,
        "stdout": "",
        "stderr": "",
        "error": error,
        "result": {},
        "metrics": [],
        "tables": [],
        "charts": [],
        "artifacts": [],
        "duration_ms": int((time.monotonic() - started_at) * 1000),
        "created_at_utc": _utc_now(),
        "metadata": {
            "runner": "zopedia_analysis.v1",
            "context_policy": "summary_only_logs",
            **({"failure_category": failure_category} if failure_category else {}),
        },
    }
    payload["llm_context_text"] = _build_llm_context(payload)
    return payload


def run_analysis_python(
    *,
    service: QueryService,
    code: str,
    objective: str = "",
    dataset_refs: list[dict[str, Any]] | None = None,
    inline_datasets: list[dict[str, Any]] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_rows: int = MAX_DATASET_ROWS,
    memory_mb: int = DEFAULT_MEMORY_MB,
    persist: bool = True,
    conn: Any | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    clean_code = normalize_analysis_code(code)
    run_id = f"zopedia_analysis::{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}::{uuid.uuid4().hex[:10]}"
    objective_text = _coerce_text(objective)
    input_refs: list[dict[str, Any]] = []
    messages: list[str] = []

    try:
        validate_analysis_code(clean_code)
    except AnalysisRejectedError as exc:
        payload = _base_result_payload(
            status="rejected",
            run_id=run_id,
            objective=objective_text,
            code=clean_code,
            input_refs=[],
            messages=[],
            started_at=started_at,
            error=str(exc),
            failure_category="analysis_code_error",
        )
        if persist:
            _persist_best_effort(payload, conn=conn)
        return payload

    try:
        frames, input_refs, messages = resolve_analysis_input_frames(
            service=service,
            dataset_refs=dataset_refs,
            inline_datasets=inline_datasets,
            max_rows=_normalize_numeric(max_rows, default=MAX_DATASET_ROWS, minimum=1, maximum=MAX_DATASET_ROWS),
        )
    except Exception as exc:
        payload = _base_result_payload(
            status="failed",
            run_id=run_id,
            objective=objective_text,
            code=clean_code,
            input_refs=input_refs,
            messages=messages,
            started_at=started_at,
            error=f"Input resolution failed: {type(exc).__name__}: {exc}",
            failure_category="analysis_input_missing",
        )
        if persist:
            _persist_best_effort(payload, conn=conn)
        return payload

    if not frames:
        payload = _base_result_payload(
            status="failed",
            run_id=run_id,
            objective=objective_text,
            code=clean_code,
            input_refs=input_refs,
            messages=messages,
            started_at=started_at,
            error="No analysis input datasets were available.",
            failure_category="analysis_input_missing",
        )
        if persist:
            _persist_best_effort(payload, conn=conn)
        return payload

    timeout = _normalize_numeric(timeout_seconds, default=DEFAULT_TIMEOUT_SECONDS, minimum=1, maximum=120)
    frame_records = {name: _frame_payload(frame) for name, frame in frames.items()}
    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_analysis_worker,
            args=(clean_code, frame_records, result_queue, _normalize_numeric(memory_mb, default=DEFAULT_MEMORY_MB, minimum=128, maximum=8192)),
    )
    process.start()
    process.join(timeout=timeout)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        payload = _base_result_payload(
            status="timeout",
            run_id=run_id,
            objective=objective_text,
            code=clean_code,
            input_refs=input_refs,
            messages=messages,
            started_at=started_at,
            error=f"Analysis exceeded {timeout}s timeout.",
            failure_category="analysis_timeout",
        )
        if persist:
            _persist_best_effort(payload, conn=conn)
        return payload

    try:
        worker_payload = result_queue.get_nowait()
    except queue.Empty:
        worker_payload = {
            "status": "failed",
            "error": f"Analysis worker exited without output (exitcode={process.exitcode}).",
            "stdout": "",
            "stderr": "",
            "result": {},
            "metrics": [],
            "tables": [],
            "charts": [],
            "artifacts": [],
        }

    status = str(worker_payload.get("status") or "failed")
    artifacts: list[dict[str, Any]] = []
    for idx, artifact in enumerate(list(worker_payload.get("artifacts") or [])[:MAX_ARTIFACTS], start=1):
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or f"artifact_{idx}")
        artifacts.append(
            {
                "artifact_id": _artifact_id(run_id, name, idx),
                "artifact_type": str(artifact.get("artifact_type") or "artifact"),
                "name": name,
                "payload": to_jsonable(artifact.get("payload") or {}),
                "preview_text": str(artifact.get("preview_text") or ""),
                "metadata": to_jsonable(artifact.get("metadata") or {}),
            }
        )

    payload = {
        "analysis_run_id": run_id,
        "status": status,
        "objective": objective_text,
        "code_hash": _sha256_text(clean_code),
        "code_text": clean_code,
        "input_refs": input_refs,
        "messages": messages,
        "stdout": str(worker_payload.get("stdout") or ""),
        "stderr": str(worker_payload.get("stderr") or ""),
        "error": str(worker_payload.get("error") or ""),
        "traceback": str(worker_payload.get("traceback") or ""),
        "result": to_jsonable(worker_payload.get("result") or {}),
        "metrics": to_jsonable(worker_payload.get("metrics") or []),
        "tables": to_jsonable(worker_payload.get("tables") or []),
        "charts": to_jsonable(worker_payload.get("charts") or []),
        "artifacts": artifacts,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
        "created_at_utc": _utc_now(),
        "metadata": {
            "runner": "zopedia_analysis.v1",
            "context_policy": "summary_only_logs",
            "frame_names": sorted(frames),
            "timeout_seconds": timeout,
            **({"failure_category": "analysis_runtime_error"} if status not in {"succeeded", "success"} else {}),
        },
    }
    payload["llm_context_text"] = _build_llm_context(payload)
    if persist:
        _persist_best_effort(payload, conn=conn)
    return payload


def _persist_best_effort(payload: dict[str, Any], *, conn: Any | None = None) -> None:
    own_conn = None
    active_conn = conn
    try:
        if active_conn is None:
            own_conn = _db_connection()
            active_conn = own_conn
        if active_conn is None:
            return
        _persist_analysis_result(active_conn, payload)
    except Exception:
        return
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception:
                pass


__all__ = [
    "ANALYSIS_ARTIFACT_TABLE",
    "ANALYSIS_RUN_TABLE",
    "AnalysisRejectedError",
    "DEFAULT_RAW_OUTPUT_MAX_CHARS",
    "MAX_RAW_OUTPUT_MAX_CHARS",
    "bootstrap_zopedia_analysis_storage",
    "build_analysis_input_profile",
    "normalize_analysis_code",
    "read_analysis_raw_output",
    "resolve_analysis_input_frames",
    "run_analysis_python",
    "validate_analysis_code",
]
