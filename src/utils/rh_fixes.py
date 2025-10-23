from .helpers import parse_portfolio_points

def get_historical_portfolio(r, login_result, span = 'all'):
    import requests
    from datetime import datetime


    token = login_result.get('access_token')
    if not token:
        raise Exception("Access token not found in login result.")
    

    headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json"
    }
    account_info = r.profiles.load_account_profile()
    account_url = account_info.get('url')
    if not account_url:
        raise Exception("Could not find account URL in profile info.")
    account_id = account_url.rstrip('/').split('/')[-1]

    # -----------------------------
    # 4. Build URL with query parameters
    # Span Options: day, week, month, 3month, ytd, year, all
    # -----------------------------
    base_url = f"https://bonfire.robinhood.com/portfolio/performance/{account_id}"
    params = {
        "chart_style": "PERFORMANCE",
        "chart_type": "historical_portfolio",
        "display_currency": "USD",
        "display_span": span,
        "include_all_hours": "true"
    }

    response = requests.get(base_url, headers=headers, params=params)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch performance data: {response.status_code}")

    data = response.json()

    print(data)

    df = parse_portfolio_points(
            data,
            line_index=0,                              # portfolio line
            base_date=datetime.today().strftime('%Y-%m-%d'),  # required if labels are time-only
            tz="US/Eastern"
            )

    print(df.head())    

    return df