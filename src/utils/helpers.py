import pandas as pd


def align_dates(portfolio_data, index_data):
    # Aligns the dates of the portfolio data with the index data
    return portfolio_data[portfolio_data.index.isin(index_data.index)]

def normalize_data(data):
    # Normalizes the data to a range of 0 to 1
    return (data - data.min()) / (data.max() - data.min())

def calculate_daily_returns(data):
    # Calculates daily returns from price data
    return data.pct_change().dropna()

def _parse_label_to_datetime(labels: pd.Series, base_date=None, tz="UTC") -> pd.Series:
    """
    Parse Robinhood chart labels which can be:
      - 'Dec 20, 2021'
      - 'Dec 20, 2021 12:00 AM'
      - '12:00 AM' (time only; requires base_date)
    Returns tz-aware pandas datetime Series.
    """
    dt = pd.to_datetime(labels, format='%b %d, %Y %I:%M %p', errors='coerce')
    mask = dt.isna()
    if mask.any():
        dt2 = pd.to_datetime(labels[mask], format='%b %d, %Y', errors='coerce')
        dt.loc[mask] = dt2

    mask = dt.isna()
    if mask.any():
        # Try generic parse
        dt3 = pd.to_datetime(labels[mask], errors='coerce')
        dt.loc[mask] = dt3

    # Time-only cases like '12:00 AM'
    mask = dt.isna()
    if mask.any():
        if base_date is None:
            # If base_date not provided, assume today's date (UTC) for time-only labels
            base_date = pd.Timestamp.utcnow().tz_localize('UTC').tz_convert(tz).normalize()
        else:
            if isinstance(base_date, _date):
                base_date = pd.Timestamp(base_date)
            base_date = pd.Timestamp(base_date)
            if base_date.tzinfo is None:
                base_date = base_date.tz_localize(tz)
            else:
                base_date = base_date.tz_convert(tz)

        time_only = labels[mask].str.fullmatch(r'\d{1,2}:\d{2}\s?(AM|PM)', case=False).fillna(False)
        if time_only.any():
            times = pd.to_datetime(labels[mask & time_only], format='%I:%M %p', errors='coerce').dt.time
            combined = [pd.Timestamp.combine(base_date.date(), t).tz_localize(base_date.tz) for t in times]
            dt.loc[mask & time_only] = combined

    # Ensure tz-aware in requested tz
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize(tz)
    else:
        dt = dt.dt.tz_convert(tz)

    return dt

def parse_portfolio_points(data: dict, line_index: int = 0, base_date=None, tz: str = "UTC") -> pd.DataFrame:
    """
    Parse Robinhood portfolio chart 'lines[...].segments[].points[]' JSON.
    Keeps all original flattened columns and adds:
      - date: tz-aware timestamp
      - current_value: numeric equity value (USD)
      - rate_of_return: cumulative return vs first point (decimal)
      - rate_of_return_text: parsed percent from UI text if available (decimal)

    Args:
      data: response.json() dict with 'lines' -> 'segments' -> 'points'
      line_index: which line to parse (0 for portfolio)
      base_date: date to use when labels are time-only (e.g., '12:00 AM')
                 Accepts datetime/date/str (ISO). If None, uses today in tz.
      tz: timezone name for tz-aware timestamps (e.g., 'US/Eastern', 'UTC')
    """
    # Flatten points
    points = pd.json_normalize(
        data['lines'][line_index],
        record_path=['segments', 'points'],
        sep='.',
        errors='ignore'
    )

    # Parse date column from label
    label_col = 'cursor_data.label.value'
    if label_col in points.columns:
        points['date'] = _parse_label_to_datetime(points[label_col], base_date=base_date, tz=tz)
    else:
        points['date'] = pd.NaT

    # Numeric current value
    val_col = 'cursor_data.price_chart_data.dollar_value.amount'
    if val_col in points.columns:
        points['current_value'] = pd.to_numeric(points[val_col], errors='coerce')
    else:
        points['current_value'] = pd.NA

    # Optional: alternate numeric fields (keep them numeric)
    alt_cols = [
        'cursor_data.price_chart_data.dollar_value_for_return.amount',
        'cursor_data.price_chart_data.dollar_value_for_rate_of_return.amount'
    ]
    for c in alt_cols:
        if c in points.columns:
            points[c] = pd.to_numeric(points[c], errors='coerce')

    # Parse rate_of_return from UI text if present: "$299.75 (0.36%)"
    txt_col = 'cursor_data.secondary_value.main.value'
    if txt_col in points.columns:
        pct = points[txt_col].astype(str).str.extract(r'\(([-+]?[\d\.]+)%\)', expand=False)
        points['rate_of_return_text'] = pd.to_numeric(pct, errors='coerce') / 100.0
    else:
        points['rate_of_return_text'] = pd.NA

    # Computed cumulative rate of return vs first observed current_value
    if points['current_value'].notna().any():
        first = points['current_value'].dropna().iloc[0]
        points['rate_of_return'] = (points['current_value'] / first) - 1.0
    else:
        points['rate_of_return'] = pd.NA

    # Sort by date if available
    if points['date'].notna().any():
        points = points.sort_values('date').reset_index(drop=True)

    return points