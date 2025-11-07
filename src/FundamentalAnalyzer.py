import os
import re
import argparse
import datetime
import pickle
from typing import Tuple, Dict, List, Optional
import shutil

import pandas as pd
import plotly.express as px
import simfin as sf
import LinkedAuth  


# Keep your template paths/logging
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'
FUNDAMENTAL_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'common', 'stock_fundamental')
try:
    print(os.listdir(DATA_STORE_CORE))
except Exception:
    pass

# ---------------- SimFin setup & loaders ----------------
def setup_simfin(api_key: Optional[str] = None, data_dir: Optional[str] = None):
    sf.set_api_key(LinkedAuth.get_creds("spectral-nature-kvault", retreive=['SimFinAPI'])[0])
    sf.set_data_dir('../../data/stock_fundamental/' if data_dir is None else data_dir)

def _purge_simfin_quarterly_cache(data_dir: Optional[str] = None):
    dd = '../../data/stock_fundamental/' if data_dir is None else data_dir
    for name in ['us-income-quarterly', 'us-balance-quarterly', 'us-cashflow-quarterly']:
        path = os.path.join(dd, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

def _has_quarters(df: pd.DataFrame) -> bool:
    try:
        base = df.reset_index()
        if 'Fiscal Period' not in base.columns:
            return False
        vals = base['Fiscal Period'].astype(str).unique()
        return any(v in {'Q1','Q2','Q3','Q4'} for v in vals)
    except Exception:
        return False

def load_quarterly_frames(refresh: bool = True, data_dir: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    try:
        if refresh:
            _purge_simfin_quarterly_cache(data_dir)
        inc = sf.load(dataset='income',   variant='quarterly', market='us')
        bal = sf.load(dataset='balance',  variant='quarterly', market='us')
        cfs = sf.load(dataset='cashflow', variant='quarterly', market='us')
    except Exception as e:
        raise RuntimeError(f"Failed to load SimFin quarterly frames (auth/network?): {e}")
    if not (_has_quarters(inc) and _has_quarters(bal) and _has_quarters(cfs)):
        raise RuntimeError("Loaded frames but no Q1–Q4 rows detected. Check dataset/variant/market.")
    return inc, bal, cfs

# ---------------- Normalization helpers ----------------
def _norm(s: str) -> str:
    s = str(s).lower().replace('&', 'and').replace('pp&e', 'ppe').replace('p p e', 'ppe')
    return re.sub(r'[^a-z0-9]+', '', s)

def _find_col_normalized(columns, candidates_norm):
    norm_map = {_norm(c): c for c in columns}
    cols_norm = list(norm_map.keys())
    for cand in candidates_norm:
        if cand in norm_map:
            return norm_map[cand]
    for cand in candidates_norm:
        for cn in cols_norm:
            if cand in cn:
                return norm_map[cn]
    return None

def _normalize_ticker(s: str) -> str:
    return re.sub(r'[^A-Z0-9]+', '', str(s).upper())

def _ticker_aliases(ticker: str):
    t = ticker.upper()
    alts = {t}
    if t == 'GOOGL':
        alts |= {'GOOG'}
    if t in {'BRK.B', 'BRK-B', 'BRKB'}:
        alts |= {'BRK.B', 'BRK-B', 'BRKB'}
    if t in {'META', 'FB'}:
        alts |= {'META', 'FB'}
    return list(alts)

# ---------------- Ticker extraction ----------------
def _extract_stmt_for_ticker(stmt_df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if stmt_df is None or getattr(stmt_df, 'empty', False):
        raise ValueError("Statement frame is empty.")
    df = stmt_df
    # MultiIndex path
    if isinstance(df.index, pd.MultiIndex):
        names = [str(n).lower() if n else '' for n in df.index.names]
        if 'ticker' in names:
            lvl = names.index('ticker')
            lv = df.index.get_level_values(lvl)
            for tk in _ticker_aliases(ticker):
                if tk in set(lv):
                    return df.xs(tk, level=lvl, drop_level=False).copy()
            norm_target = _normalize_ticker(ticker)
            for val in pd.Index(lv).unique():
                if _normalize_ticker(val) == norm_target:
                    return df.xs(val, level=lvl, drop_level=False).copy()
    # Column path
    base = df.reset_index()
    for cand in ['Ticker', 'ticker', 'Symbol']:
        if cand in base.columns:
            for tk in _ticker_aliases(ticker):
                sub = base[base[cand] == tk]
                if not sub.empty:
                    return sub.copy()
            norm_target = _normalize_ticker(ticker)
            tmp = base[base[cand].astype(str).str.upper().str.replace(r'[^A-Z0-9]+', '', regex=True) == norm_target]
            if not tmp.empty:
                return tmp.copy()
    raise ValueError(f"No rows for {ticker} in statement.")

# ---------------- Strict quarterly shaping (require Q1–Q4) ----------------
def _qtr_prepare(df: pd.DataFrame) -> pd.DataFrame:
    """
    Strict quarterly shaping. Avoid fragile Int64 casts; keep Year/QuarterNum numeric via to_numeric.
    """
    if 'Report Date' not in df.columns:
        return pd.DataFrame()
    base = df.copy()

    if 'Fiscal Period' not in base.columns:
        return pd.DataFrame()

    qmask = base['Fiscal Period'].astype(str).isin(['Q1', 'Q2', 'Q3', 'Q4'])
    if not qmask.any():
        return pd.DataFrame()
    base = base[qmask].copy()

    base['Report Date'] = pd.to_datetime(base['Report Date'], errors='coerce')

    # Prefer Fiscal Year for off-calendar companies (e.g., STX)
    if 'Fiscal Year' in base.columns:
        base['Year'] = pd.to_numeric(base['Fiscal Year'], errors='coerce')
    else:
        base['Year'] = base['Report Date'].dt.year

    base['Quarter'] = base['Fiscal Period'].astype(str).str.extract(r'(Q[1-4])', expand=False)
    base['QuarterNum'] = pd.to_numeric(base['Quarter'].str[-1], errors='coerce')

    base = base.sort_values('Report Date').drop_duplicates(subset=['Year', 'Quarter'], keep='last')

    # Use Fiscal Year (if present) for label to avoid mislabeling FY that crosses calendar years
    year_label = (base['Fiscal Year'].astype(int).astype(str)
                  if 'Fiscal Year' in base.columns else base['Report Date'].dt.year.astype(int).astype(str))
    base['YearQ'] = year_label + base['Quarter']
    return base

# ---------------- Column resolvers ----------------
def _find_cfo_col(df_cols):
    return _find_col_normalized(df_cols, [
        'netcashfromoperatingactivities', 'cashfromoperatingactivities', 'netcashprovidedbyoperatingactivities',
        'operatingcashflow', 'cashflowfromoperatingactivities', 'netcashflowfromoperatingactivities',
        'netcffromoperatingactivities', 'cfo'
    ])

def _find_capex_single_col(df_cols):
    return _find_col_normalized(df_cols, [
        'capitalexpenditures', 'capitalexpenditure', 'capex',
        'purchaseofppe', 'purchaseofpropertyplantandequipment', 'purchasepropertyplantandequipment',
        'additionspropertyplantandequipment', 'investmentinppe',
        'purchaseofintangibleassets', 'purchaseofintangibles', 'additionsintangibleassets'
    ])

def _find_change_fai_col(df_cols):
    return _find_col_normalized(df_cols, [
        'changeinfixedassetsandintangibles', 'changeinpropertyplantandequipmentandintangibles'
    ])

def _income_cols(df_cols):
    return {
        'Revenue': _find_col_normalized(df_cols, ['revenue', 'totalrevenue', 'sales']),
        'Operating Income': _find_col_normalized(df_cols, ['operatingincomeebit', 'operatingincome', 'ebit']),
        'Net Income': _find_col_normalized(df_cols, ['netincome', 'netincomecommon']),
    }

def _balance_cols(df_cols):
    return {
        'Total Assets': _find_col_normalized(df_cols, ['totalassets']),
        'Total Liabilities': _find_col_normalized(df_cols, ['totalliabilities']),
        'Total Equity': _find_col_normalized(df_cols, ['totalequity', 'shareholdersequity']),
    }

# ---------------- Per-statement quarterly frames ----------------
def quarterly_income_for_ticker(ticker: str, income: pd.DataFrame) -> pd.DataFrame:
    df = _extract_stmt_for_ticker(income, ticker)
    return _qtr_prepare(df)

def quarterly_balance_for_ticker(ticker: str, balance: pd.DataFrame) -> pd.DataFrame:
    df = _extract_stmt_for_ticker(balance, ticker)
    return _qtr_prepare(df)

def quarterly_cash_for_ticker(ticker: str, cashflw: pd.DataFrame) -> pd.DataFrame:
    df = _extract_stmt_for_ticker(cashflw, ticker)
    df = _qtr_prepare(df)
    cols = list(df.columns)
    cfo_col = _find_cfo_col(cols)
    if not cfo_col:
        raise ValueError(f"CFO column not found for {ticker}.")
    capex_col = _find_capex_single_col(cols)
    change_fai_col = _find_change_fai_col(cols)

    out = pd.DataFrame({
        'Report Date': df['Report Date'],
        'Year': df['Year'],             # keep numeric without forcing Int64
        'Quarter': df['Quarter'],
        'QuarterNum': df['QuarterNum'], # numeric (float/int) is fine for filtering
        'YearQ': df['YearQ'],
        'Ticker': ticker,
        'CFO': pd.to_numeric(df[cfo_col], errors='coerce')
    })
    if capex_col:
        out['CapEx'] = pd.to_numeric(df[capex_col], errors='coerce')
    elif change_fai_col:
        out['CapEx'] = -pd.to_numeric(df[change_fai_col], errors='coerce')
    else:
        out['CapEx'] = pd.NA

    out['Free Cash Flow'] = out['CFO'] - pd.to_numeric(out['CapEx'], errors='coerce')
    return out

# ---------------- Year-quarter filtering ----------------
def _parse_year_quarter(yq: Optional[str]):
    if not yq:
        return None
    m = re.match(r'^\s*(\d{4})\s*[- ]?\s*[Qq]\s*([1-4])\s*$', str(yq))
    if not m:
        raise ValueError("year_quarter must look like '2018Q1' or '2018-Q3'")
    return int(m.group(1)), int(m.group(2))

def _apply_since_yq(df: pd.DataFrame, yq):
    if not yq or df.empty:
        return df
    y, q = yq
    return df[(df['Year'] > y) | ((df['Year'] == y) & (df['QuarterNum'] >= q))].copy()

# ---------------- Public entry point ----------------
def run_quarterly_comparison(
    tickers: List[str],
    since: Optional[str] = None,
    api_key: Optional[str] = None,
    data_dir: Optional[str] = None,
    plot: bool = True
):
    """
    Compare 3 balance, 3 income, and 3 cash flow metrics quarterly for given tickers.
    since: 'YYYYQn' (e.g., '2018Q1') to filter from that quarter inclusive.
    Returns: (tidy_frames, pivot_tables, errors)
    """
    setup_simfin(api_key=api_key, data_dir=data_dir or '../../data/stock_fundamental/')
    income, balance, cashflw = load_quarterly_frames()
    since_yq = _parse_year_quarter(since)

    results = {'income': [], 'balance': [], 'cashflow': []}
    errors: Dict[str, str] = {}

    for t in tickers:
        try:
            inc = quarterly_income_for_ticker(t, income)
            bal = quarterly_balance_for_ticker(t, balance)
            cfs = quarterly_cash_for_ticker(t, cashflw)
            if since_yq:
                inc = _apply_since_yq(inc, since_yq)
                bal = _apply_since_yq(bal, since_yq)
                cfs = _apply_since_yq(cfs, since_yq)

            inc_cols = _income_cols(list(inc.columns))
            bal_cols = _balance_cols(list(bal.columns))

            for label, col in inc_cols.items():
                if col:
                    tmp = inc[['Report Date', 'Year', 'Quarter', 'QuarterNum', 'YearQ', col]].copy()
                    tmp['Metric'] = label
                    tmp['Ticker'] = t
                    tmp.rename(columns={col: 'Value'}, inplace=True)
                    results['income'].append(tmp)

            for label, col in bal_cols.items():
                if col:
                    tmp = bal[['Report Date', 'Year', 'Quarter', 'QuarterNum', 'YearQ', col]].copy()
                    tmp['Metric'] = label
                    tmp['Ticker'] = t
                    tmp.rename(columns={col: 'Value'}, inplace=True)
                    results['balance'].append(tmp)

            cfs2 = cfs[['Report Date', 'Year', 'Quarter', 'QuarterNum', 'YearQ', 'Ticker', 'CFO', 'CapEx', 'Free Cash Flow']].copy()
            results['cashflow'].append(
                cfs2.melt(id_vars=['Report Date', 'Year', 'Quarter', 'QuarterNum', 'YearQ', 'Ticker'],
                          var_name='Metric', value_name='Value')
            )
        except Exception as e:
            errors[t] = str(e)

    income_df = pd.concat(results['income'], ignore_index=True) if results['income'] else pd.DataFrame()
    balance_df = pd.concat(results['balance'], ignore_index=True) if results['balance'] else pd.DataFrame()
    cashflow_df = pd.concat(results['cashflow'], ignore_index=True) if results['cashflow'] else pd.DataFrame()

    def _pivots_by_metric(df):
        piv = {}
        if df.empty:
            return piv
        for metric in sorted(df['Metric'].dropna().unique()):
            sub = df[df['Metric'] == metric]
            piv[metric] = sub.pivot_table(index='YearQ', columns='Ticker', values='Value', aggfunc='last').sort_index()
        return piv

    income_pivots = _pivots_by_metric(income_df)
    balance_pivots = _pivots_by_metric(balance_df)
    cashflow_pivots = _pivots_by_metric(cashflow_df)

    if plot:
        def _plot_tidy(df, title_prefix):
            if df.empty:
                return
            for metric in sorted(df['Metric'].dropna().unique()):
                sub = df[df['Metric'] == metric].copy().sort_values('Report Date')
                fig = px.line(sub, x='Report Date', y='Value', color='Ticker',
                              title=f'{title_prefix} (Quarterly) - {metric}', markers=True)
                fig.update_layout(legend_orientation='h')
                fig.show()
        _plot_tidy(income_df, 'Income Statement')
        _plot_tidy(balance_df, 'Balance Sheet')
        _plot_tidy(cashflow_df, 'Cash Flow')

    return {'income': income_df, 'balance': balance_df, 'cashflow': cashflow_df}, \
           {'income': income_pivots, 'balance': balance_pivots, 'cashflow': cashflow_pivots}, \
           errors

# ---------------- CLI and caching (kept from template) ----------------
def _sanitize_for_filename(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '_', s)

def _is_cache_stale(file_path: str, max_age_seconds: int = 3 * 3600) -> bool:
    if not os.path.exists(file_path):
        return True
    file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
    return (datetime.datetime.now() - file_mod_time).total_seconds() > max_age_seconds

def main_cli(tickers_csv: str, since: Optional[str], cache_mode: str):
    if not os.path.exists(FUNDAMENTAL_HISTORY_STORE):
        os.makedirs(FUNDAMENTAL_HISTORY_STORE, exist_ok=True)

    tickers = [t.strip().upper() for t in tickers_csv.split(',') if t.strip()]
    since_key = since or 'ALL'
    fname = f"fundamental_quarterly_{_sanitize_for_filename('-'.join(tickers))}_{_sanitize_for_filename(since_key)}.pkl"
    cache_file_path = os.path.join(FUNDAMENTAL_HISTORY_STORE, fname)

    if cache_mode == 'local' and os.path.exists(cache_file_path) and not _is_cache_stale(cache_file_path):
        print("Loading quarterly fundamentals from local cache...")
        print(cache_file_path)
        with open(cache_file_path, 'rb') as f:
            bundle = pickle.load(f)
        return bundle

    # Run comparison and cache
    tidy, pivots, errs = run_quarterly_comparison(
        tickers=tickers,
        since=since,
        api_key=None,                       # pass explicit key if desired
        data_dir='../../data/stock_fundamental/',
        plot=True
    )
    bundle = {'tidy': tidy, 'pivots': pivots, 'errors': errs}

    if cache_mode == 'refresh' or _is_cache_stale(cache_file_path):
        print("Refreshing quarterly fundamentals cache...")
        with open(cache_file_path, 'wb') as f:
            pickle.dump(bundle, f)

    if errs:
        print("Errors:", errs)
    return bundle

def main(tickers_csv: str, cache_mode: str = 'normal', since: Optional[str] = '2018Q1', plot: bool = True):
    """
    Back-compat wrapper for front_end_app.app usage:
      - tickers_csv: 'MSFT' or 'AAPL,MSFT'
      - cache_mode: 'local' | 'refresh' | 'normal'
      - since: e.g., '2018Q1'
      - plot: pass-through to run_quarterly_comparison
    Returns the same bundle dict shape as main_cli.
    """
    if plot is not True:
        tidy, pivots, errs = run_quarterly_comparison(
            tickers=[t.strip().upper() for t in tickers_csv.split(',') if t.strip()],
            since=since,
            api_key=None,
            data_dir='../../data/stock_fundamental/',
            plot=plot
        )
        return {'tidy': tidy, 'pivots': pivots, 'errors': errs}
    return main_cli(tickers_csv, since, cache_mode)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Quarterly Fundamental Comparison (SimFin)')
    parser.add_argument('--tickers', required=True, help='Comma-separated tickers (e.g., AAPL,MSFT,NVDA)')
    parser.add_argument('--since', default='2018Q1', help="Start quarter, e.g., 2018Q1")
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the data/cache')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache if fresh')

    args = parser.parse_args()

    if args.force_refresh and args.force_local:
        print("Error: Cannot force refresh and use local cache at the same time.")
        raise SystemExit(1)

    if args.force_refresh:
        cache_mode = "refresh"
    else:
        cache_mode = "normal"
        if args.force_local:
            cache_mode = "local"

    main_cli(args.tickers, args.since, cache_mode)