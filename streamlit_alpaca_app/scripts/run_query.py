from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data_access.contracts import QueryRequest, to_jsonable
from data_access.query_service import QueryService
from presentation.plotly import render_chart_model


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query the shared data access layer for datasets or chart models.")
    parser.add_argument("--operation", choices=["capabilities", "dataset", "chart"], required=True)
    parser.add_argument("--name", default="", help="Dataset or chart name.")
    parser.add_argument("--params-json", default="{}", help="JSON object with request parameters.")
    parser.add_argument(
        "--render",
        choices=["canonical", "plotly"],
        default="canonical",
        help="For chart operations, optionally render Plotly JSON instead of the canonical chart model.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        params = json.loads(args.params_json)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid JSON for --params-json: {exc}"}))
        return 2

    service = QueryService.from_environment()
    request = QueryRequest(operation=args.operation, name=args.name, params=params)

    try:
        response = service.execute(request)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1

    payload = response.to_dict()
    if args.render == "plotly":
        if response.result_type != "chart_model":
            print(json.dumps({"error": "--render plotly is only valid for chart operations."}))
            return 2
        figure = render_chart_model(response.payload)
        payload = {
            "request": request.to_dict(),
            "result_type": "plotly_figure",
            "payload": to_jsonable(figure.to_dict()),
            "provenance": response.provenance.to_dict() if response.provenance is not None else None,
            "messages": list(response.messages),
        }

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
