"""Rolling held-out risk forecasts. No strategy-alpha or profitability claim.

Input: retained adjusted daily bars (symbol,timestamp,close). Selection of these
currently surviving assets limits generalization; this is a risk diagnostic.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf


def evaluate(prices: pd.DataFrame) -> dict:
    prices = prices.copy()
    prices['date'] = pd.to_datetime(prices.timestamp, utc=True).dt.tz_convert('America/New_York').dt.date.astype(str)
    closes = prices.drop_duplicates(['date', 'symbol']).pivot(index='date', columns='symbol', values='close').sort_index()
    returns = closes.pct_change(fill_method=None)
    symbols = sorted(returns.columns)
    records = []
    for stop in range(252, len(returns) - 21 + 1, 21):
        train = returns.iloc[stop - 252:stop].dropna()
        future = returns.iloc[stop:stop + 21].dropna()
        if len(train) < 126 or len(future) < 15:
            continue
        slow = LedoitWolf().fit(train).covariance_
        fast = LedoitWolf().fit(train.tail(63)).covariance_
        sample = train.cov().to_numpy()
        diagonal = np.diag(np.diag(sample))
        equal = np.ones(len(symbols)) / len(symbols)
        inverse = 1 / np.sqrt(np.diag(sample)); inverse /= inverse.sum()
        mixed = equal * np.where(np.arange(len(symbols)) % 2, -1, 1)
        for book, weights in [('equal_capital', equal), ('inverse_volatility', inverse), ('long_short', mixed)]:
            realized = float(np.var(future.to_numpy() @ weights, ddof=1))
            forecasts = {name: float(weights @ matrix @ weights) for name, matrix in
                         [('sample', sample), ('diagonal', diagonal), ('shrinkage_252', slow), ('shrinkage_63', fast)]}
            forecasts['fast_slow_envelope'] = max(forecasts['shrinkage_252'], forecasts['shrinkage_63'])
            for method, variance in forecasts.items():
                ratio = realized / variance
                records.append({'start': returns.index[stop], 'end': returns.index[stop + 20],
                    'split': 'holdout' if returns.index[stop] >= '2024-01-01' else 'development',
                    'book': book, 'method': method, 'predicted_vol': np.sqrt(variance * 252),
                    'realized_vol': np.sqrt(realized * 252), 'qlike': ratio - np.log(ratio) - 1})
    frame = pd.DataFrame(records)
    summary = []
    for (split, method, book), group in frame.groupby(['split', 'method', 'book']):
        ratio = group.realized_vol / group.predicted_vol
        summary.append({'split': split, 'method': method, 'book': book, 'periods': len(group),
                        'mean_qlike': float(group.qlike.mean()),
                        'median_realized_to_predicted_vol': float(ratio.median()),
                        'fraction_above_1_5x_forecast': float((ratio > 1.5).mean()),
                        'worst_realized_to_predicted_vol': float(ratio.max())})
    return {'symbols': symbols, 'start': returns.index[0], 'end': returns.index[-1],
            'training_sessions': 252, 'holdout_sessions': 21, 'stride': 21,
            'holdout_start': '2024-01-01', 'observations': records, 'summary': summary,
            'scope': 'Risk forecast diagnostic; selected surviving assets; no costs or alpha evaluation.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('prices', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = evaluate(pd.read_parquet(args.prices))
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({'symbols': result['symbols'], 'start': result['start'], 'end': result['end'],
                      'summary': result['summary']}, indent=2))
