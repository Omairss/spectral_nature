"""Shared JSON transport contracts; no inference or prose assertions."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json

import numpy as np
import pandas as pd
import pytest

from data_access.contracts import DataProvenance, QueryRequest, QueryResponse
from services.json_utils import frame_to_records, to_jsonable, to_list


@dataclass
class _Payload:
    observations: object
    at: datetime


def test_nested_data_values_have_one_strict_json_representation():
    at = datetime(2026, 10, 18, tzinfo=timezone.utc)
    payload = _Payload(
        pd.Series([
            {"array": np.array([[1, 2], [3, 4]]), "missing": pd.NA},
            pd.Index([date(2026, 10, 18), pd.NaT]),
            np.array(5),
            pd.Timedelta(days=1),
        ], dtype=object),
        at,
    )
    result = to_jsonable(payload)
    assert json.loads(json.dumps(result, allow_nan=False)) == {
        "observations": [
            {"array": [[1, 2], [3, 4]], "missing": None},
            ["2026-10-18", None],
            5,
            "P1DT0H0M0S",
        ],
        "at": at.isoformat(),
    }
    assert to_jsonable(result) == result


@pytest.mark.parametrize("missing", [None, pd.NA, pd.NaT, float("nan"), np.datetime64("NaT"), Decimal("NaN"), float("inf"), -float("inf")])
def test_missing_and_nonfinite_values_are_json_null(missing):
    assert json.dumps(to_jsonable(missing), allow_nan=False) == "null"


def test_dataframes_and_query_responses_use_the_same_boundary():
    frame = pd.DataFrame({"at": [pd.NaT], "value": [np.float64(1.5)], "context": [{"source": pd.NA}]})
    expected = [{"at": None, "value": 1.5, "context": {"source": None}}]
    response = QueryResponse(QueryRequest("table", "observations"), "table", frame, DataProvenance("cached", ("observations",)))
    assert frame_to_records(frame) == to_list(frame) == expected
    assert json.loads(json.dumps(response.to_dict(), allow_nan=False))["payload"] == expected
    assert frame_to_records(pd.DataFrame()) == []


def test_container_conversion_keeps_scalar_values_and_rejects_unknown_objects():
    assert to_list(np.array(7)) == [7]
    assert to_list(pd.Index([1, 2])) == [1, 2]
    assert to_jsonable({1: False, "zero": 0, "empty": ""}) == {"1": False, "zero": 0, "empty": ""}
    with pytest.raises(TypeError):
        json.dumps(to_jsonable(object()), allow_nan=False)
