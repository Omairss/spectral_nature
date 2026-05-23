from __future__ import annotations

import pytest

from data_access.contracts import DataProvenance, ResolvedPayload
from data_access.query_service import QueryService
from services import zopedia_analysis


def _service() -> QueryService:
    return QueryService(data_access=object())


def test_run_analysis_python_executes_basic_eda_with_inline_dataset():
    code = """
prices = datasets["prices"].copy()
prices["return"] = prices["close"].pct_change()
add_metric("rows", len(prices))
add_metric("mean_return", float(prices["return"].dropna().mean()))
add_table("price_preview", prices[["date", "close"]].head(3))
add_chart("close_path", kind="line", x=prices["date"].tolist(), y=prices["close"].tolist())
"""

    result = zopedia_analysis.run_analysis_python(
        service=_service(),
        objective="basic price EDA",
        code=code,
        inline_datasets=[
            {
                "name": "prices",
                "rows": [
                    {"date": "2026-01-01", "close": 100.0},
                    {"date": "2026-01-02", "close": 102.0},
                    {"date": "2026-01-03", "close": 101.0},
                ],
            }
        ],
        timeout_seconds=10,
        persist=False,
    )

    assert result["status"] == "succeeded"
    assert result["analysis_run_id"].startswith("zopedia_analysis::")
    assert {metric["name"] for metric in result["metrics"]} == {"rows", "mean_return"}
    assert result["tables"][0]["name"] == "price_preview"
    assert result["charts"][0]["name"] == "close_path"
    assert "Zopedia analysis run" in result["llm_context_text"]


def test_run_analysis_python_supports_basic_sklearn_models():
    pytest.importorskip("sklearn")
    code = """
from sklearn.linear_model import LinearRegression

df = datasets["sample"]
model = LinearRegression()
model.fit(df[["x"]], df["y"])
add_metric("coef", float(model.coef_[0]))
add_metric("intercept", float(model.intercept_))
"""

    result = zopedia_analysis.run_analysis_python(
        service=_service(),
        objective="fit a simple regression",
        code=code,
        inline_datasets=[
            {
                "name": "sample",
                "rows": [
                    {"x": 1.0, "y": 3.0},
                    {"x": 2.0, "y": 5.0},
                    {"x": 3.0, "y": 7.0},
                    {"x": 4.0, "y": 9.0},
                ],
            }
        ],
        timeout_seconds=15,
        persist=False,
    )

    metrics = {metric["name"]: metric["value"] for metric in result["metrics"]}
    assert result["status"] == "succeeded"
    assert metrics["coef"] == pytest.approx(2.0)
    assert metrics["intercept"] == pytest.approx(1.0)


def test_run_analysis_python_rejects_shell_and_filesystem_escape():
    result = zopedia_analysis.run_analysis_python(
        service=_service(),
        objective="bad code",
        code="import os\nos.system('echo nope')",
        inline_datasets=[{"name": "sample", "rows": [{"x": 1}]}],
        persist=False,
    )

    assert result["status"] == "rejected"
    assert "Import 'os' is not allowed" in result["error"]
    assert result["metadata"]["failure_category"] == "analysis_code_error"


def test_run_analysis_python_classifies_missing_inputs():
    result = zopedia_analysis.run_analysis_python(
        service=_service(),
        objective="needs input",
        code="add_metric('rows', len(datasets['prices']))",
        persist=False,
    )

    assert result["status"] == "failed"
    assert result["metadata"]["failure_category"] == "analysis_input_missing"
    assert "Failure category: analysis_input_missing" in result["llm_context_text"]


def test_resolve_analysis_input_frames_keeps_duplicate_dataset_aliases_distinct():
    class _FakeService:
        def fetch_dataset(self, name, params):
            ticker = params.get("ticker")
            return ResolvedPayload(
                payload=[{"ticker": ticker, "close": 100.0}],
                provenance=DataProvenance(mode="computed", datasets=(name,), details={}),
            )

    frames, input_refs, messages = zopedia_analysis.resolve_analysis_input_frames(
        service=_FakeService(),
        dataset_refs=[
            {"name": "price_history", "params": {"ticker": "SPY", "days": 60}},
            {"name": "price_history", "params": {"ticker": "QQQ", "days": 60}},
        ],
    )

    assert messages == []
    assert sorted(frames) == ["price_history", "price_history_days_60_ticker_qqq"]
    assert [ref["alias"] for ref in input_refs] == ["price_history", "price_history_days_60_ticker_qqq"]


def test_run_analysis_python_allows_safe_pandas_rename_and_exposes_stdout():
    result = zopedia_analysis.run_analysis_python(
        service=_service(),
        objective="rename columns",
        code="""
prices = datasets["prices"].rename(columns={"close": "last"})
print(f"rows={len(prices)} last={prices['last'].iloc[-1]}")
add_table("renamed_prices", prices)
""",
        inline_datasets=[
            {
                "name": "prices",
                "rows": [{"date": "2026-01-01", "close": 10}, {"date": "2026-01-02", "close": 11}],
            }
        ],
        timeout_seconds=10,
        persist=False,
    )

    assert result["status"] == "succeeded"
    assert "rows=2 last=11" in result["stdout"]
    assert "Stdout:" in result["llm_context_text"]
    assert "line(s)" in result["llm_context_text"]
    assert "char(s)" in result["llm_context_text"]
    assert "analysis.read_raw_output" in result["llm_context_text"]
    assert "rows=2 last=11" not in result["llm_context_text"]


def test_read_analysis_raw_output_returns_bounded_explicit_log_text():
    class _Cursor:
        def __init__(self, row):
            self.row = row

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return self.row

    class _Conn:
        def __init__(self, row):
            self.row = row

        def cursor(self):
            return _Cursor(self.row)

    row = (
        "zopedia_analysis::raw",
        "succeeded",
        "debug output",
        "first line\nrows=2 last=11\nthird line",
        "",
        "",
        "",
        "2026-05-22T00:00:00+00:00",
    )

    result = zopedia_analysis.read_analysis_raw_output(
        analysis_run_id="zopedia_analysis::raw",
        stream="stdout",
        max_chars=28,
        conn=_Conn(row),
    )

    assert result["status"] == "ok"
    assert result["stream"] == "stdout"
    assert result["total_chars"] > result["returned_chars"]
    assert result["truncated"] is True
    assert "rows=2 last=11" in result["raw_text"]
    assert "Raw excerpt:" in result["llm_context_text"]
    assert "rows=2 last=11" in result["llm_context_text"]


def test_run_analysis_python_normalizes_single_line_block_indentation():
    result = zopedia_analysis.run_analysis_python(
        service=_service(),
        objective="skip small frame",
        code="""
prices = datasets["prices"]
for _alias in ["prices"]:
    if len(prices) < 3:
continue
    add_metric("rows", len(prices))
""",
        inline_datasets=[
            {
                "name": "prices",
                "rows": [{"close": 10}, {"close": 11}, {"close": 12}],
            }
        ],
        timeout_seconds=10,
        persist=False,
    )

    assert result["status"] == "succeeded"
    assert result["metrics"][0]["name"] == "rows"


def test_normalize_analysis_code_recovers_common_unindented_for_loop():
    code = """
values = [1, 2, 3]
total = 0
for value in values:
increment = value * 2
if increment < 3:
continue
total = total + increment
add_metric("total", total)
"""
    normalized = zopedia_analysis.normalize_analysis_code(code)

    assert "    increment = value * 2" in normalized
    assert "        continue" in normalized
    zopedia_analysis.validate_analysis_code(normalized)


def test_build_analysis_input_profile_includes_columns_dtypes_and_sample():
    profile = zopedia_analysis.build_analysis_input_profile(
        service=_service(),
        inline_datasets=[
            {
                "name": "prices",
                "rows": [{"date": "2026-01-01", "close": 10}, {"date": "2026-01-02", "close": 11}],
            }
        ],
    )

    assert profile["status"] == "ok"
    assert profile["frames"][0]["alias"] == "prices"
    assert profile["frames"][0]["columns"] == ["date", "close"]
    assert profile["frames"][0]["sample"][0]["close"] == 10
