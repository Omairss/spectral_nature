from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable

import pandas as pd

from .llm import AzureOpenAIChatJSONClient, OpenAIChatJSONClient
from .pipeline_store import load_latest_dataset_frame
from .runtime_policy import taxonomy_classifier_policy
from .secrets import postgres_connect_timeout_seconds, resolve_secret_value


try:
    import psycopg
except Exception:
    psycopg = None


LLMClient = OpenAIChatJSONClient | AzureOpenAIChatJSONClient
TaxonomyProgressCallback = Callable[[dict[str, Any]], None]

ENTITY_TAXONOMY_COLUMNS = [
    "symbol",
    "exchange",
    "security_name",
    "listing_source",
    "is_active",
    "is_etf",
    "asset_class",
    "security_type",
    "sector",
    "industry",
    "peer_group_name",
    "peer_group_id",
    "country",
    "commodity_role",
    "rates_role",
    "defensive_role",
    "macro_role_tags",
    "business_role_tags",
    "source_of_truth",
    "label_provider",
    "label_confidence",
    "is_curated",
    "override_reason",
    "classifier_model",
    "classifier_version",
    "updated_at_utc",
]

DEFAULT_ALLOWED_SECTORS = [
    "Unknown",
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Credit",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
    "Broad Market",
    "Commodities",
    "Rates",
]


def _allowed_sectors() -> list[str]:
    policy = taxonomy_classifier_policy()
    configured = [str(value).strip() for value in list(getattr(policy, "allowed_sectors", ()) or ()) if str(value).strip()]
    sectors = configured or list(DEFAULT_ALLOWED_SECTORS)
    if "Unknown" not in sectors:
        sectors = ["Unknown"] + [value for value in sectors if value != "Unknown"]
    return sectors


ALLOWED_SECTORS = _allowed_sectors()

SOURCE_PRIORITY = {
    "manual_override": 0,
    "llm_taxonomy": 1,
    "listing_metadata": 2,
    "default": 3,
}

CONFIDENCE_PRIORITY = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "unknown": 3,
}

ACTIVE_TAXONOMY_SOURCES = {
    "manual_override",
    "llm_taxonomy",
    "listing_metadata",
    "default",
}


def _coerce_text(value: object) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def _normalize_symbol(value: object) -> str:
    return _coerce_text(value).upper()


def _safe_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, (tuple, set, pd.Series, pd.Index)):
        items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = [text]
        items = parsed if isinstance(parsed, list) else [parsed]
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        text = _coerce_text(item)
        if text:
            out.append(text)
    return out


def _normalize_slug(value: object) -> str:
    lowered = _coerce_text(value).lower()
    if not lowered:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def _normalize_tag_list(value: object) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in _safe_list(value):
        slug = _normalize_slug(item)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def _humanize_slug(value: object) -> str:
    slug = _normalize_slug(value)
    if not slug:
        return ""
    words: list[str] = []
    for token in slug.split("_"):
        if not token:
            continue
        if token in {"ai", "api", "ev", "gpu", "ipo", "oil", "reit", "saas", "us"}:
            words.append(token.upper())
        elif token == "and":
            words.append("&")
        else:
            words.append(token.title())
    return " ".join(words).replace(" & ", " & ").strip()


DASHBOARD_BUSINESS_LENS_TAGS: dict[str, str] = {
    "housing": "Housing",
    "retail": "Retail",
    "media": "Media",
    "social_media_entertainment": "Social Media & Entertainment",
    "advertising": "Advertising",
    "payments_and_commerce": "Payments & Commerce",
    "travel_mobility": "Travel & Mobility",
    "healthcare_life_sciences": "Healthcare & Life Sciences",
}

DASHBOARD_BUSINESS_LENS_SECTORS: dict[str, str] = {
    "Energy": "Commodity",
    "Materials": "Commodity",
    "Health Care": "Healthcare & Life Sciences",
}


def _utc_now_ts() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _peer_group_name(industry: object, sector: object, business_role_tags: object) -> str:
    industry_text = _coerce_text(industry)
    sector_text = _coerce_text(sector)
    tags = _normalize_tag_list(business_role_tags)
    if industry_text and industry_text != "Unknown":
        return industry_text
    if sector_text and sector_text != "Unknown":
        return sector_text
    if tags:
        return _humanize_slug(tags[0])
    return "Market"


def _peer_group_id(industry: object, sector: object, asset_class: object) -> str:
    industry_text = _coerce_text(industry)
    sector_text = _coerce_text(sector)
    asset_class_text = _coerce_text(asset_class)
    if industry_text and industry_text != "Unknown":
        return industry_text
    if sector_text and sector_text != "Unknown":
        return sector_text
    return asset_class_text or "unknown"


def _listing_lookup(listings: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if not isinstance(listings, pd.DataFrame) or listings.empty:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, row in listings.iterrows():
        symbol = _normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        out[symbol] = row.to_dict()
    return out


def _security_type_from_listing(listing_row: dict[str, Any] | None) -> str:
    row = listing_row or {}
    if bool(row.get("is_etf")):
        return "etf"
    name = _coerce_text(row.get("security_name")).lower()
    if any(token in name for token in (" etf", " fund", " trust")):
        return "etf"
    if "reit" in name:
        return "reit"
    if "adr" in name or "american depositary" in name:
        return "adr"
    return "common_stock"


def _asset_class_from_security_type(security_type: object) -> str:
    security_text = _coerce_text(security_type)
    if security_text == "etf":
        return "etf"
    if security_text == "reit":
        return "real_estate"
    return "equity"


def _commodity_role_from_name(name: object) -> str:
    lowered = _coerce_text(name).lower()
    if not lowered:
        return ""
    if "oil" in lowered or "brent" in lowered or "wti" in lowered:
        return "oil"
    return lowered.replace(" ", "_")


def _taxonomy_row(
    symbol: str,
    *,
    listing_row: dict[str, Any] | None = None,
    asset_class: str = "equity",
    security_type: str = "common_stock",
    sector: str = "Unknown",
    industry: str = "Unknown",
    country: str = "US",
    commodity_role: str = "",
    rates_role: str = "",
    defensive_role: str = "",
    macro_role_tags: list[str] | None = None,
    business_role_tags: list[str] | None = None,
    source_of_truth: str = "default",
    label_provider: str = "default",
    label_confidence: str = "low",
    is_curated: bool = False,
    override_reason: str = "",
    classifier_model: str = "",
    classifier_version: str = "",
) -> dict[str, Any]:
    listing = listing_row or {}
    normalized_commodity_role = _normalize_slug(commodity_role)
    normalized_rates_role = _normalize_slug(rates_role)
    normalized_defensive_role = _normalize_slug(defensive_role)
    normalized_macro_tags = _normalize_tag_list(macro_role_tags)
    normalized_business_tags = _normalize_tag_list(business_role_tags)
    return {
        "symbol": _normalize_symbol(symbol),
        "exchange": _coerce_text(listing.get("exchange")),
        "security_name": _coerce_text(listing.get("security_name")),
        "listing_source": _coerce_text(listing.get("source_file")),
        "is_active": True,
        "is_etf": bool(listing.get("is_etf", security_type == "etf")),
        "asset_class": _coerce_text(asset_class) or "equity",
        "security_type": _coerce_text(security_type) or "common_stock",
        "sector": _coerce_text(sector) or "Unknown",
        "industry": _coerce_text(industry) or "Unknown",
        "peer_group_name": _peer_group_name(industry, sector, business_role_tags),
        "peer_group_id": _peer_group_id(industry, sector, asset_class),
        "country": _coerce_text(country) or "US",
        "commodity_role": normalized_commodity_role,
        "rates_role": normalized_rates_role,
        "defensive_role": normalized_defensive_role,
        "macro_role_tags": normalized_macro_tags,
        "business_role_tags": normalized_business_tags,
        "source_of_truth": _coerce_text(source_of_truth) or "default",
        "label_provider": _coerce_text(label_provider) or "default",
        "label_confidence": (_coerce_text(label_confidence) or "unknown").lower(),
        "is_curated": bool(is_curated),
        "override_reason": _coerce_text(override_reason),
        "classifier_model": _coerce_text(classifier_model),
        "classifier_version": _coerce_text(classifier_version),
        "updated_at_utc": _utc_now_ts(),
    }


def empty_entity_taxonomy_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=ENTITY_TAXONOMY_COLUMNS)


def normalize_entity_taxonomy_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return empty_entity_taxonomy_frame()

    out = frame.copy()
    for column in ENTITY_TAXONOMY_COLUMNS:
        if column not in out.columns:
            if column in {"macro_role_tags", "business_role_tags"}:
                out[column] = [[] for _ in range(len(out))]
            elif column in {"is_active"}:
                out[column] = True
            elif column in {"is_etf", "is_curated"}:
                out[column] = False
            elif column == "updated_at_utc":
                out[column] = _utc_now_ts()
            else:
                out[column] = ""

    out["symbol"] = out["symbol"].map(_normalize_symbol)
    out = out[out["symbol"].ne("")].copy()
    if out.empty:
        return empty_entity_taxonomy_frame()

    text_columns = [
        "exchange",
        "security_name",
        "listing_source",
        "asset_class",
        "security_type",
        "sector",
        "industry",
        "peer_group_name",
        "peer_group_id",
        "country",
        "commodity_role",
        "rates_role",
        "defensive_role",
        "source_of_truth",
        "label_provider",
        "label_confidence",
        "override_reason",
        "classifier_model",
        "classifier_version",
    ]
    for column in text_columns:
        out[column] = out[column].map(_coerce_text)

    for column in ("macro_role_tags", "business_role_tags"):
        out[column] = out[column].map(_normalize_tag_list)

    def _coerce_bool(value: object, default: bool) -> bool:
        if pd.isna(value):
            return default
        if isinstance(value, bool):
            return value
        text = _coerce_text(value).lower()
        if not text:
            return default
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(value)

    out["is_active"] = out["is_active"].map(lambda value: _coerce_bool(value, True))
    out["is_etf"] = out["is_etf"].map(lambda value: _coerce_bool(value, False))
    out["is_curated"] = out["is_curated"].map(lambda value: _coerce_bool(value, False))

    out["security_type"] = out.apply(
        lambda row: _coerce_text(row.get("security_type")) or ("etf" if bool(row.get("is_etf")) else "common_stock"),
        axis=1,
    )
    out["asset_class"] = out.apply(
        lambda row: _coerce_text(row.get("asset_class")) or _asset_class_from_security_type(row.get("security_type")),
        axis=1,
    )
    allowed_sector_set = set(_allowed_sectors())
    out["sector"] = out["sector"].map(lambda value: value if value in allowed_sector_set else (value or "Unknown"))
    out["industry"] = out["industry"].map(lambda value: value or "Unknown")
    out["country"] = out["country"].map(lambda value: value or "US")
    out["commodity_role"] = out["commodity_role"].map(_normalize_slug)
    out["rates_role"] = out["rates_role"].map(_normalize_slug)
    out["defensive_role"] = out["defensive_role"].map(_normalize_slug)
    out["peer_group_name"] = out.apply(
        lambda row: _coerce_text(row.get("peer_group_name")) or _peer_group_name(
            row.get("industry"),
            row.get("sector"),
            row.get("business_role_tags"),
        ),
        axis=1,
    )
    out["peer_group_id"] = out.apply(
        lambda row: _coerce_text(row.get("peer_group_id")) or _peer_group_id(
            row.get("industry"),
            row.get("sector"),
            row.get("asset_class"),
        ),
        axis=1,
    )
    out["updated_at_utc"] = pd.to_datetime(out["updated_at_utc"], utc=True, errors="coerce")
    out["updated_at_utc"] = out["updated_at_utc"].fillna(_utc_now_ts())
    out = out.drop_duplicates(subset=["symbol"], keep="last").reset_index(drop=True)
    return out[ENTITY_TAXONOMY_COLUMNS].copy()


def merge_entity_taxonomy_frames(*frames: pd.DataFrame | None) -> pd.DataFrame:
    parts = [
        normalize_entity_taxonomy_frame(frame)
        for frame in frames
        if isinstance(frame, pd.DataFrame) and not frame.empty
    ]
    if not parts:
        return empty_entity_taxonomy_frame()

    combined = pd.concat(parts, ignore_index=True, sort=False)
    combined["_source_priority"] = combined["source_of_truth"].map(lambda value: SOURCE_PRIORITY.get(_coerce_text(value), 99))
    combined["_coverage_gap_priority"] = _coverage_gap_mask(combined).map(lambda missing: 1 if bool(missing) else 0)
    combined["_confidence_priority"] = combined["label_confidence"].map(lambda value: CONFIDENCE_PRIORITY.get(_coerce_text(value).lower(), 99))
    combined = combined.sort_values(
        ["symbol", "_source_priority", "_coverage_gap_priority", "_confidence_priority", "updated_at_utc"],
        ascending=[True, True, True, True, False],
        na_position="last",
    )
    combined = combined.drop_duplicates(subset=["symbol"], keep="first")
    return combined[ENTITY_TAXONOMY_COLUMNS].reset_index(drop=True)


def _db_connection() -> Any | None:
    conn_str = resolve_secret_value(
        ["POSTGRES_CONNECTION_STRING"],
        secret_name_env="POSTGRES_CONNECTION_STRING_SECRET",
        default_secret_name="postgres-connection-string",
    )
    if not conn_str or psycopg is None:
        return None
    try:
        return psycopg.connect(conn_str, connect_timeout=postgres_connect_timeout_seconds())
    except Exception:
        return None


def bootstrap_entity_taxonomy_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_taxonomy_labels (
                symbol TEXT PRIMARY KEY,
                exchange TEXT,
                security_name TEXT,
                listing_source TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_etf BOOLEAN NOT NULL DEFAULT FALSE,
                asset_class TEXT NOT NULL,
                security_type TEXT NOT NULL,
                sector TEXT NOT NULL,
                industry TEXT NOT NULL,
                peer_group_name TEXT NOT NULL,
                peer_group_id TEXT NOT NULL,
                country TEXT NOT NULL,
                commodity_role TEXT,
                rates_role TEXT,
                defensive_role TEXT,
                macro_role_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                business_role_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                source_of_truth TEXT NOT NULL,
                label_provider TEXT NOT NULL,
                label_confidence TEXT NOT NULL,
                is_curated BOOLEAN NOT NULL DEFAULT FALSE,
                override_reason TEXT,
                classifier_model TEXT,
                classifier_version TEXT,
                updated_at_utc TIMESTAMPTZ NOT NULL,
                created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entity_taxonomy_active_source
            ON entity_taxonomy_labels (is_active, source_of_truth, updated_at_utc DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entity_taxonomy_peer_group
            ON entity_taxonomy_labels (peer_group_id, symbol)
            """
        )
    conn.commit()


def upsert_entity_taxonomy_frame(conn: Any, frame: pd.DataFrame) -> None:
    normalized = normalize_entity_taxonomy_frame(frame)
    if normalized.empty:
        return

    sql = """
        INSERT INTO entity_taxonomy_labels (
            symbol, exchange, security_name, listing_source, is_active, is_etf,
            asset_class, security_type, sector, industry, peer_group_name, peer_group_id,
            country, commodity_role, rates_role, defensive_role, macro_role_tags, business_role_tags,
            source_of_truth, label_provider, label_confidence, is_curated, override_reason,
            classifier_model, classifier_version, updated_at_utc
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s::jsonb, %s::jsonb,
            %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (symbol) DO UPDATE SET
            exchange = EXCLUDED.exchange,
            security_name = EXCLUDED.security_name,
            listing_source = EXCLUDED.listing_source,
            is_active = EXCLUDED.is_active,
            is_etf = EXCLUDED.is_etf,
            asset_class = EXCLUDED.asset_class,
            security_type = EXCLUDED.security_type,
            sector = EXCLUDED.sector,
            industry = EXCLUDED.industry,
            peer_group_name = EXCLUDED.peer_group_name,
            peer_group_id = EXCLUDED.peer_group_id,
            country = EXCLUDED.country,
            commodity_role = EXCLUDED.commodity_role,
            rates_role = EXCLUDED.rates_role,
            defensive_role = EXCLUDED.defensive_role,
            macro_role_tags = EXCLUDED.macro_role_tags,
            business_role_tags = EXCLUDED.business_role_tags,
            source_of_truth = EXCLUDED.source_of_truth,
            label_provider = EXCLUDED.label_provider,
            label_confidence = EXCLUDED.label_confidence,
            is_curated = EXCLUDED.is_curated,
            override_reason = EXCLUDED.override_reason,
            classifier_model = EXCLUDED.classifier_model,
            classifier_version = EXCLUDED.classifier_version,
            updated_at_utc = EXCLUDED.updated_at_utc
    """
    rows = [
        (
            row["symbol"],
            row["exchange"],
            row["security_name"],
            row["listing_source"],
            bool(row["is_active"]),
            bool(row["is_etf"]),
            row["asset_class"],
            row["security_type"],
            row["sector"],
            row["industry"],
            row["peer_group_name"],
            row["peer_group_id"],
            row["country"],
            row["commodity_role"],
            row["rates_role"],
            row["defensive_role"],
            json.dumps(row["macro_role_tags"]),
            json.dumps(row["business_role_tags"]),
            row["source_of_truth"],
            row["label_provider"],
            row["label_confidence"],
            bool(row["is_curated"]),
            row["override_reason"],
            row["classifier_model"],
            row["classifier_version"],
            row["updated_at_utc"].to_pydatetime() if isinstance(row["updated_at_utc"], pd.Timestamp) else row["updated_at_utc"],
        )
        for _, row in normalized.iterrows()
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()


def deactivate_missing_taxonomy_symbols(conn: Any, active_symbols: list[str]) -> None:
    symbols = [_normalize_symbol(symbol) for symbol in list(active_symbols or []) if _normalize_symbol(symbol)]
    with conn.cursor() as cur:
        if symbols:
            cur.execute(
                """
                UPDATE entity_taxonomy_labels
                SET is_active = FALSE,
                    updated_at_utc = %s
                WHERE is_active = TRUE
                  AND NOT (symbol = ANY(%s))
                """,
                (_utc_now_ts().to_pydatetime(), symbols),
            )
        else:
            cur.execute(
                """
                UPDATE entity_taxonomy_labels
                SET is_active = FALSE,
                    updated_at_utc = %s
                WHERE is_active = TRUE
                """,
                (_utc_now_ts().to_pydatetime(),),
            )
    conn.commit()


def _rows_from_query(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    if not rows:
        return empty_entity_taxonomy_frame()
    frame = pd.DataFrame(rows, columns=ENTITY_TAXONOMY_COLUMNS)
    return normalize_entity_taxonomy_frame(frame)


def fetch_entity_taxonomy_frame(symbols: list[str] | None = None) -> pd.DataFrame:
    conn = _db_connection()
    if conn is None:
        return empty_entity_taxonomy_frame()
    try:
        with conn.cursor() as cur:
            base_query = """
                SELECT
                    symbol, exchange, security_name, listing_source, is_active, is_etf,
                    asset_class, security_type, sector, industry, peer_group_name, peer_group_id,
                    country, commodity_role, rates_role, defensive_role, macro_role_tags, business_role_tags,
                    source_of_truth, label_provider, label_confidence, is_curated, override_reason,
                    classifier_model, classifier_version, updated_at_utc
                FROM entity_taxonomy_labels
                WHERE is_active = TRUE
            """
            normalized_symbols = [_normalize_symbol(symbol) for symbol in list(symbols or []) if _normalize_symbol(symbol)]
            if normalized_symbols:
                cur.execute(
                    base_query + " AND symbol = ANY(%s)",
                    (normalized_symbols,),
                )
            else:
                cur.execute(base_query)
            rows = cur.fetchall() or []
    except Exception:
        return empty_entity_taxonomy_frame()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return _rows_from_query(rows)


def load_entity_taxonomy_frame(symbols: list[str] | None = None) -> pd.DataFrame:
    db_frame = fetch_entity_taxonomy_frame(symbols)
    if not db_frame.empty:
        db_frame = db_frame[db_frame["source_of_truth"].isin(ACTIVE_TAXONOMY_SOURCES)].copy()
        return db_frame

    materialized, _ = load_latest_dataset_frame("entity_taxonomy_labels")
    materialized = normalize_entity_taxonomy_frame(materialized)
    if materialized.empty:
        return materialized
    materialized = materialized[materialized["source_of_truth"].isin(ACTIVE_TAXONOMY_SOURCES)].copy()
    if materialized.empty:
        return materialized
    materialized = materialized[materialized["is_active"]].copy()
    if symbols:
        normalized = {_normalize_symbol(symbol) for symbol in list(symbols or []) if _normalize_symbol(symbol)}
        materialized = materialized[materialized["symbol"].isin(normalized)].copy()
    return materialized.reset_index(drop=True)


def taxonomy_lookup_by_symbol(symbols: list[str]) -> dict[str, dict[str, Any]]:
    frame = load_entity_taxonomy_frame(symbols)
    if frame.empty:
        return {}
    return {
        _normalize_symbol(row.get("symbol")): row.to_dict()
        for _, row in frame.iterrows()
        if _normalize_symbol(row.get("symbol"))
    }


def business_focus_label_from_taxonomy_row(row: dict[str, Any] | None) -> str:
    payload = row or {}
    for candidate in _normalize_tag_list(payload.get("business_role_tags")):
        label = _humanize_slug(candidate)
        if label:
            return label

    for field in ("peer_group_name", "industry", "sector"):
        label = _coerce_text(payload.get(field))
        if label and label not in {"All Market", "Market", "Unknown"}:
            return label

    for field in ("commodity_role", "rates_role", "defensive_role"):
        label = _humanize_slug(payload.get(field))
        if label:
            return label
    return ""


def dashboard_business_lens_from_taxonomy_row(row: dict[str, Any] | None) -> str:
    payload = row or {}

    if _coerce_text(payload.get("commodity_role")):
        return "Commodity"

    for candidate in _normalize_tag_list(payload.get("business_role_tags")):
        label = DASHBOARD_BUSINESS_LENS_TAGS.get(candidate)
        if label:
            return label

    sector = _coerce_text(payload.get("sector"))
    return DASHBOARD_BUSINESS_LENS_SECTORS.get(sector, "")


def build_listing_default_taxonomy_frame(listings: pd.DataFrame) -> pd.DataFrame:
    listing_lookup = _listing_lookup(listings)
    rows = []
    for symbol, listing in listing_lookup.items():
        security_type = _security_type_from_listing(listing)
        rows.append(
            _taxonomy_row(
                symbol,
                listing_row=listing,
                asset_class=_asset_class_from_security_type(security_type),
                security_type=security_type,
                sector="Unknown",
                industry="Unknown",
                source_of_truth="listing_metadata",
                label_provider="listing_metadata",
                label_confidence="low",
                is_curated=False,
                override_reason="Listing metadata only; sector and industry are not yet assigned.",
                classifier_version="listing_v1",
            )
        )
    return normalize_entity_taxonomy_frame(pd.DataFrame(rows))


def build_seed_entity_taxonomy_frame(listings: pd.DataFrame | None = None) -> pd.DataFrame:
    return empty_entity_taxonomy_frame()


def _chunked(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    chunk_size = max(int(size), 1)
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def _prepare_listings_frame(listings: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(listings, pd.DataFrame) or listings.empty:
        return pd.DataFrame(columns=["symbol"])
    prepared = listings.copy()
    if "symbol" not in prepared.columns:
        prepared["symbol"] = ""
    prepared["symbol"] = prepared["symbol"].map(_normalize_symbol)
    prepared = prepared[prepared["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    return prepared


def _coverage_gap_mask(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["sector"].map(_coerce_text).eq("Unknown")
        | frame["industry"].map(_coerce_text).eq("Unknown")
        | frame["business_role_tags"].map(lambda value: len(_normalize_tag_list(value)) == 0)
    )


def _coverage_gap_rows(frame: pd.DataFrame, *, active_symbols: set[str] | None = None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return empty_entity_taxonomy_frame()
    out = normalize_entity_taxonomy_frame(frame)
    mask = _coverage_gap_mask(out)
    if active_symbols is not None:
        mask = mask & out["symbol"].isin(active_symbols)
    return out[mask].copy()


def _client_model_name(llm_client: LLMClient) -> str:
    config = getattr(llm_client, "config", None)
    if config is None:
        return ""
    deployment = _coerce_text(getattr(config, "deployment", ""))
    if deployment:
        return deployment
    return _coerce_text(getattr(config, "model", ""))


def _taxonomy_llm_schema(*, allow_unknown: bool = True, require_business_tags: bool = False) -> dict[str, Any]:
    active_allowed_sectors = _allowed_sectors()
    allowed_sectors = list(active_allowed_sectors) if allow_unknown else [value for value in active_allowed_sectors if value != "Unknown"]
    business_role_schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 4,
    }
    if require_business_tags:
        business_role_schema["minItems"] = 1
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "symbol": {"type": "string"},
                        "sector": {"type": "string", "enum": allowed_sectors},
                        "industry": {"type": "string"},
                        "commodity_role": {"type": "string"},
                        "rates_role": {"type": "string"},
                        "defensive_role": {"type": "string"},
                        "macro_role_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 4,
                        },
                        "business_role_tags": business_role_schema,
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "symbol",
                        "sector",
                        "industry",
                        "commodity_role",
                        "rates_role",
                        "defensive_role",
                        "macro_role_tags",
                        "business_role_tags",
                        "confidence",
                        "notes",
                    ],
                },
            }
        },
        "required": ["classifications"],
    }


def classify_listings_with_llm(
    listings: pd.DataFrame,
    llm_client: LLMClient | None,
    *,
    batch_size: int | None = None,
    allow_unknown: bool = True,
    progress_callback: TaxonomyProgressCallback | None = None,
) -> pd.DataFrame:
    if llm_client is None or not isinstance(listings, pd.DataFrame) or listings.empty:
        return empty_entity_taxonomy_frame()

    prepared = listings.copy()
    prepared["symbol"] = prepared["symbol"].map(_normalize_symbol)
    prepared = prepared[prepared["symbol"].ne("")].drop_duplicates(subset=["symbol"], keep="first")
    if prepared.empty:
        return empty_entity_taxonomy_frame()

    policy = taxonomy_classifier_policy()
    effective_batch_size = policy.default_batch_size if batch_size is None else max(int(batch_size), 1)
    listing_lookup = _listing_lookup(prepared)
    schema = _taxonomy_llm_schema(allow_unknown=allow_unknown, require_business_tags=not allow_unknown)
    if allow_unknown:
        system_prompt = policy.allow_unknown_prompt
    else:
        system_prompt = policy.force_classify_prompt

    requests_payload = [
        {
            "symbol": row["symbol"],
            "exchange": _coerce_text(row.get("exchange")),
            "security_name": _coerce_text(row.get("security_name")),
            "is_etf": bool(row.get("is_etf")),
        }
        for _, row in prepared.iterrows()
    ]

    rows: list[dict[str, Any]] = []
    chunks = _chunked(requests_payload, effective_batch_size)
    total_chunks = len(chunks)
    active_allowed_sectors = _allowed_sectors()
    allowed_sectors = list(active_allowed_sectors) if allow_unknown else [value for value in active_allowed_sectors if value != "Unknown"]
    print(
        f"[info] taxonomy llm classify symbols={len(requests_payload)} "
        f"batches={total_chunks} batch_size={effective_batch_size} allow_unknown={allow_unknown}"
    )
    if progress_callback is not None:
        progress_callback(
            {
                "event": "classify_start",
                "allow_unknown": allow_unknown,
                "total_symbols": len(requests_payload),
                "total_batches": total_chunks,
                "batch_size": effective_batch_size,
            }
        )
    total_classified = 0
    for index, chunk in enumerate(chunks, start=1):
        batch_symbols = [item.get("symbol", "") for item in chunk]
        first_symbol = _coerce_text(batch_symbols[0]) if batch_symbols else ""
        last_symbol = _coerce_text(batch_symbols[-1]) if batch_symbols else ""
        print(
            f"[info] taxonomy llm batch_start index={index}/{total_chunks} "
            f"size={len(chunk)} allow_unknown={allow_unknown} first={first_symbol} last={last_symbol}"
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "batch_start",
                    "allow_unknown": allow_unknown,
                    "batch_index": index,
                    "total_batches": total_chunks,
                    "batch_size": len(chunk),
                    "total_symbols": len(requests_payload),
                    "total_classified": total_classified,
                    "first_symbol": first_symbol,
                    "last_symbol": last_symbol,
                }
            )
        try:
            response = llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=(
                    "Classify these listings into sector, industry, business_role_tags, macro_role_tags, and optional macro roles.\n"
                    f"Allowed sectors: {json.dumps(allowed_sectors)}\n"
                    "Return concise reusable tags, not company-specific prose.\n"
                    f"{json.dumps(chunk, ensure_ascii=False, default=str, indent=2)}"
                ),
                schema_name="entity_taxonomy_batch",
                schema=schema,
            )
        except Exception as exc:
            print(
                f"[error] taxonomy llm batch_failed index={index}/{total_chunks} "
                f"size={len(chunk)} first={first_symbol} last={last_symbol} "
                f"error={type(exc).__name__}: {exc}"
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "batch_failed",
                        "allow_unknown": allow_unknown,
                        "batch_index": index,
                        "total_batches": total_chunks,
                        "batch_size": len(chunk),
                        "total_symbols": len(requests_payload),
                        "total_classified": total_classified,
                        "first_symbol": first_symbol,
                        "last_symbol": last_symbol,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            raise

        classified_in_batch = 0
        for item in list(response.get("classifications") or []):
            symbol = _normalize_symbol(item.get("symbol"))
            if not symbol or symbol not in listing_lookup:
                continue
            listing = listing_lookup.get(symbol, {})
            security_type = _security_type_from_listing(listing)
            rows.append(
                _taxonomy_row(
                    symbol,
                    listing_row=listing,
                    asset_class=_asset_class_from_security_type(security_type),
                    security_type=security_type,
                    sector=_coerce_text(item.get("sector")) or "Unknown",
                    industry=_coerce_text(item.get("industry")) or "Unknown",
                    country=policy.default_country,
                    commodity_role=item.get("commodity_role"),
                    rates_role=item.get("rates_role"),
                    defensive_role=item.get("defensive_role"),
                    macro_role_tags=item.get("macro_role_tags"),
                    business_role_tags=item.get("business_role_tags"),
                    source_of_truth="llm_taxonomy",
                    label_provider="llm",
                    label_confidence=(_coerce_text(item.get("confidence")) or "low").lower(),
                    is_curated=False,
                    override_reason=_coerce_text(item.get("notes")),
                    classifier_model=_client_model_name(llm_client),
                    classifier_version=policy.classifier_version,
                )
            )
            classified_in_batch += 1
            total_classified += 1

        print(
            f"[info] taxonomy llm batch_complete index={index}/{total_chunks} "
            f"requested={len(chunk)} classified={classified_in_batch} total_classified={total_classified}"
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "batch_complete",
                    "allow_unknown": allow_unknown,
                    "batch_index": index,
                    "total_batches": total_chunks,
                    "batch_size": len(chunk),
                    "classified_in_batch": classified_in_batch,
                    "total_symbols": len(requests_payload),
                    "total_classified": total_classified,
                    "first_symbol": first_symbol,
                    "last_symbol": last_symbol,
                }
            )

    print(f"[info] taxonomy llm classify_complete rows={len(rows)} allow_unknown={allow_unknown}")
    if progress_callback is not None:
        progress_callback(
            {
                "event": "classify_complete",
                "allow_unknown": allow_unknown,
                "total_symbols": len(requests_payload),
                "total_batches": total_chunks,
                "total_classified": total_classified,
                "row_count": len(rows),
            }
        )

    return normalize_entity_taxonomy_frame(pd.DataFrame(rows))


def build_entity_taxonomy_snapshot(
    listings: pd.DataFrame,
    *,
    existing_frame: pd.DataFrame | None = None,
    llm_client: LLMClient | None = None,
    llm_batch_size: int | None = None,
    llm_max_symbols: int | None = None,
    progress_callback: TaxonomyProgressCallback | None = None,
) -> pd.DataFrame:
    policy = taxonomy_classifier_policy()
    effective_llm_batch_size = policy.default_batch_size if llm_batch_size is None else max(int(llm_batch_size), 1)
    prepared_listings = _prepare_listings_frame(listings)
    defaults = build_listing_default_taxonomy_frame(prepared_listings)
    if defaults.empty:
        return defaults

    existing = normalize_entity_taxonomy_frame(existing_frame)
    existing = existing[existing["source_of_truth"].isin({"llm_taxonomy"})].copy()
    merged = merge_entity_taxonomy_frames(defaults, existing)

    unresolved = merged[
        merged["sector"].eq("Unknown")
        & merged["industry"].eq("Unknown")
    ].copy()
    unresolved_symbols = unresolved["symbol"].tolist()
    if llm_max_symbols is not None:
        unresolved_symbols = unresolved_symbols[: max(int(llm_max_symbols), 0)]

    print(
        f"[info] taxonomy snapshot listings={len(defaults)} existing_dynamic={len(existing)} "
        f"unresolved={len(unresolved_symbols)} llm_enabled={llm_client is not None} "
        f"llm_batch_size={effective_llm_batch_size} "
        f"llm_max_symbols={llm_max_symbols if llm_max_symbols is not None else 'all'}"
    )
    if progress_callback is not None:
        progress_callback(
            {
                "event": "snapshot_prepare",
                "listing_count": len(defaults),
                "existing_dynamic_count": len(existing),
                "unresolved_count": len(unresolved_symbols),
                "llm_enabled": llm_client is not None,
                "llm_batch_size": effective_llm_batch_size,
                "llm_max_symbols": llm_max_symbols,
            }
        )

    llm_rows = empty_entity_taxonomy_frame()
    if llm_client is not None and unresolved_symbols:
        unresolved_listings = prepared_listings[prepared_listings["symbol"].isin(set(unresolved_symbols))].copy()
        llm_rows = classify_listings_with_llm(
            unresolved_listings,
            llm_client,
            batch_size=effective_llm_batch_size,
            progress_callback=progress_callback,
        )
    elif unresolved_symbols:
        sample = ", ".join(unresolved_symbols[:10])
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "snapshot_failed",
                    "reason": "missing_llm_client",
                    "unresolved_count": len(unresolved_symbols),
                    "sample_symbols": unresolved_symbols[:10],
                }
            )
        raise RuntimeError(
            f"taxonomy refresh requires dynamic classification but no LLM client is available; unresolved symbols={len(unresolved_symbols)} sample={sample}"
        )

    active_symbols = set(defaults["symbol"].tolist())
    merged = merge_entity_taxonomy_frames(defaults, existing, llm_rows)
    repair_targets = _coverage_gap_rows(merged, active_symbols=active_symbols)
    repair_rows = empty_entity_taxonomy_frame()
    if not repair_targets.empty:
        repair_symbols = repair_targets["symbol"].tolist()
        if llm_max_symbols is not None:
            repair_symbols = repair_symbols[: max(int(llm_max_symbols), 0)]
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "repair_prepare",
                    "repair_target_count": len(repair_symbols),
                    "sample_symbols": repair_symbols[:10],
                }
            )
        if llm_client is None:
            sample = ", ".join(repair_symbols[:10])
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "snapshot_failed",
                        "reason": "missing_llm_client_repair",
                        "unresolved_count": len(repair_symbols),
                        "sample_symbols": repair_symbols[:10],
                    }
                )
            raise RuntimeError(
                f"taxonomy repair pass requires LLM client but it is unavailable; unresolved symbols={len(repair_symbols)} sample={sample}"
            )
        repair_listings = prepared_listings[prepared_listings["symbol"].isin(set(repair_symbols))].copy()
        repair_rows = classify_listings_with_llm(
            repair_listings,
            llm_client,
            batch_size=effective_llm_batch_size,
            allow_unknown=False,
            progress_callback=progress_callback,
        )

    retry_rows = empty_entity_taxonomy_frame()
    retry_batch_sizes: list[int] = []
    for value in list(getattr(policy, "repair_retry_batch_sizes", ()) or ()):
        try:
            batch_size = max(int(value), 1)
        except Exception:
            continue
        if batch_size == effective_llm_batch_size or batch_size in retry_batch_sizes:
            continue
        retry_batch_sizes.append(batch_size)

    merged = merge_entity_taxonomy_frames(defaults, existing, llm_rows, repair_rows, retry_rows)
    merged = merged[merged["symbol"].isin(active_symbols)].copy()
    merged["is_active"] = True
    unresolved_final = _coverage_gap_rows(merged, active_symbols=active_symbols)

    for retry_index, retry_batch_size in enumerate(retry_batch_sizes, start=1):
        if unresolved_final.empty:
            break
        retry_symbols = unresolved_final["symbol"].astype(str).tolist()
        if llm_max_symbols is not None:
            retry_symbols = retry_symbols[: max(int(llm_max_symbols), 0)]
        if not retry_symbols:
            break
        if llm_client is None:
            break
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "repair_retry_prepare",
                    "retry_index": retry_index,
                    "retry_total": len(retry_batch_sizes),
                    "batch_size": retry_batch_size,
                    "repair_target_count": len(retry_symbols),
                    "sample_symbols": retry_symbols[:10],
                }
            )
        print(
            f"[info] taxonomy repair retry index={retry_index}/{len(retry_batch_sizes)} "
            f"batch_size={retry_batch_size} targets={len(retry_symbols)}"
        )
        retry_listings = prepared_listings[prepared_listings["symbol"].isin(set(retry_symbols))].copy()
        retry_frame = classify_listings_with_llm(
            retry_listings,
            llm_client,
            batch_size=retry_batch_size,
            allow_unknown=False,
            progress_callback=progress_callback,
        )
        retry_rows = merge_entity_taxonomy_frames(retry_rows, retry_frame)
        merged = merge_entity_taxonomy_frames(defaults, existing, llm_rows, repair_rows, retry_rows)
        merged = merged[merged["symbol"].isin(active_symbols)].copy()
        merged["is_active"] = True
        unresolved_final = _coverage_gap_rows(merged, active_symbols=active_symbols)
        print(
            f"[info] taxonomy repair retry_complete index={retry_index}/{len(retry_batch_sizes)} "
            f"batch_size={retry_batch_size} retry_rows={len(retry_frame)} unresolved={len(unresolved_final)}"
        )
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "repair_retry_complete",
                    "retry_index": retry_index,
                    "retry_total": len(retry_batch_sizes),
                    "batch_size": retry_batch_size,
                    "retry_rows": len(retry_frame),
                    "unresolved_count": len(unresolved_final),
                    "sample_symbols": unresolved_final["symbol"].astype(str).head(10).tolist(),
                }
            )

    if progress_callback is not None:
        progress_callback(
            {
                "event": "snapshot_finalize",
                "listing_count": len(defaults),
                "initial_llm_rows": len(llm_rows),
                "repair_rows": len(repair_rows),
                "repair_retry_rows": len(retry_rows),
            }
        )
    merged = merge_entity_taxonomy_frames(defaults, existing, llm_rows, repair_rows, retry_rows)
    merged = merged[merged["symbol"].isin(active_symbols)].copy()
    merged["is_active"] = True
    unresolved_final = _coverage_gap_rows(merged, active_symbols=active_symbols)
    if not unresolved_final.empty:
        sample = ", ".join(unresolved_final["symbol"].astype(str).head(10).tolist())
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "snapshot_failed",
                    "reason": "incomplete_coverage",
                    "unresolved_count": len(unresolved_final),
                    "sample_symbols": unresolved_final["symbol"].astype(str).head(10).tolist(),
                }
            )
        raise RuntimeError(
            f"taxonomy refresh ended with incomplete coverage rows={len(unresolved_final)} sample={sample}"
        )
    if progress_callback is not None:
        progress_callback(
            {
                "event": "snapshot_complete",
                "row_count": len(merged),
            }
        )
    return normalize_entity_taxonomy_frame(merged)


def informative_taxonomy_row(row: dict[str, Any] | None) -> bool:
    payload = row or {}
    source = _coerce_text(payload.get("source_of_truth"))
    sector = _coerce_text(payload.get("sector"))
    industry = _coerce_text(payload.get("industry"))
    if source and source not in {"default", "listing_metadata"}:
        return True
    if sector and sector != "Unknown":
        return True
    if industry and industry != "Unknown":
        return True
    if _safe_list(payload.get("business_role_tags")) or _safe_list(payload.get("macro_role_tags")):
        return True
    if _coerce_text(payload.get("commodity_role")) or _coerce_text(payload.get("rates_role")):
        return True
    return False


__all__ = [
    "ALLOWED_SECTORS",
    "ENTITY_TAXONOMY_COLUMNS",
    "bootstrap_entity_taxonomy_tables",
    "business_focus_label_from_taxonomy_row",
    "dashboard_business_lens_from_taxonomy_row",
    "build_entity_taxonomy_snapshot",
    "build_listing_default_taxonomy_frame",
    "build_seed_entity_taxonomy_frame",
    "classify_listings_with_llm",
    "deactivate_missing_taxonomy_symbols",
    "empty_entity_taxonomy_frame",
    "fetch_entity_taxonomy_frame",
    "informative_taxonomy_row",
    "load_entity_taxonomy_frame",
    "merge_entity_taxonomy_frames",
    "normalize_entity_taxonomy_frame",
    "taxonomy_lookup_by_symbol",
    "upsert_entity_taxonomy_frame",
]
