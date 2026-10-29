"""Portfolio-aware volatility budgeting, independent of narrative confidence.

Risk budgets are user preferences. Ledoit-Wolf estimates covariance; it does not
estimate alpha or establish an optimal growth bet. See the sizing design record.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf


@dataclass(frozen=True)
class RiskPolicy:
    position_volatility_pct: float = 2.0
    portfolio_volatility_pct: float = 10.0
    max_position_pct: float = 10.0
    max_gross_pct: float = 100.0

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if not math.isfinite(value) or value <= 0 or value > 100:
                raise ValueError(f'{key} must be between 0 and 100 percent.')
        if self.position_volatility_pct > self.portfolio_volatility_pct:
            raise ValueError('Position risk cannot exceed the portfolio risk budget.')


def risk_after_quantity(risk: dict, quantity: str) -> dict:
    """Value the exact reviewed shares, including rounding and execution reserve."""
    proposed = np.array([risk['portfolio_weights'].get(key, 0) for key in risk['symbols']], dtype=float)
    index = risk['symbols'].index(risk['symbol'])
    notional = float(quantity) * risk['price']
    proposed[index] += (1 if risk['stance'] == 'long' else -1) * notional / risk['equity']
    result = {**risk, 'suggested_quantity': quantity, 'suggested_notional': notional,
              'target_weight_pct': float(proposed[index]) * 100,
              'portfolio_volatility_after_pct': float(np.sqrt(max(0, proposed @ np.array(risk['covariance']) @ proposed))) * 100}
    retained = risk.get('retained_returns')
    if retained is not None:
        historical = np.array(retained['values']) @ proposed
        tail = historical[historical <= np.quantile(historical, .05)]
        result['historical_daily_expected_shortfall_pct'] = -float(tail.mean()) * 100
    return result


def recommend_position(*, returns: pd.DataFrame, weights: dict[str, float], symbol: str,
                       stance: str, equity: float, buying_power: float, cash: float,
                       price: float, policy: RiskPolicy) -> dict:
    """Recommend a target weight with the rest of the portfolio held fixed.

    Solve the covariance quadratic for the candidate weight under a portfolio
    volatility limit, then intersect position-risk, concentration, gross exposure
    and funding constraints. No fabricated win probability or expected return.
    """
    policy.validate()
    if stance not in {'long', 'short'} or not all(math.isfinite(x) and x > 0 for x in (equity, price)):
        raise ValueError('Sizing needs a direction, positive equity and a current price.')
    if not all(math.isfinite(x) for x in (buying_power, cash, *weights.values())):
        raise ValueError('Account balances are unavailable.')
    symbols = sorted({symbol, *weights})
    if any(key not in returns for key in symbols):
        raise ValueError('Daily return history is missing for a portfolio position.')
    # A gap must not become a zero return or a multi-session return labelled daily.
    matrix = returns[symbols].replace([np.inf, -np.inf], np.nan).dropna().tail(252)
    if len(matrix) < 126:
        raise ValueError('Sizing requires at least 126 shared daily return observations.')
    estimator = LedoitWolf().fit(matrix.to_numpy(dtype=float))
    covariance = estimator.covariance_ * 252
    index = symbols.index(symbol)
    sigma = float(np.sqrt(covariance[index, index]))
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('The risk estimate is unavailable.')
    current = np.array([weights.get(key, 0.0) for key in symbols], dtype=float)
    others = current.copy()
    others[index] = 0
    a = float(covariance[index, index])
    b = float((covariance @ others)[index])
    c = float(others @ covariance @ others) - (policy.portfolio_volatility_pct / 100) ** 2
    discriminant = b * b - a * c
    if discriminant < 0:
        raise ValueError('The existing portfolio exceeds the risk budget; reduce risk before adding this position.')
    lower, upper = (-b - math.sqrt(discriminant)) / a, (-b + math.sqrt(discriminant)) / a
    sign = 1 if stance == 'long' else -1
    caps = {
        'position_volatility': policy.position_volatility_pct / 100 / sigma,
        'position_limit': policy.max_position_pct / 100,
        'gross_limit': max(0, policy.max_gross_pct / 100 - float(np.abs(others).sum())),
        'portfolio_volatility': upper if sign > 0 else -lower,
    }
    # Do not spend short-sale proceeds or assume margin capacity is cash. The
    # review is deliberately unlevered; Alpaca still validates the actual order.
    available = max(0, min(buying_power, cash)) / equity
    if sign > 0:
        caps['funding'] = max(0, current[index] + available)
    else:
        caps['funding'] = max(0, -current[index] + max(0, buying_power) / equity)
    cap = max(0, min(caps.values()))
    target_weight = sign * cap
    proposed = others.copy()
    proposed[index] = target_weight
    before = float(np.sqrt(max(0, current @ covariance @ current)))
    after = float(np.sqrt(max(0, proposed @ covariance @ proposed)))
    if target_weight < lower - 1e-10 or target_weight > upper + 1e-10:
        raise ValueError('No position in this direction fits the portfolio risk budget.')
    current_weight = float(current[index])
    # A fresh approval cannot silently reduce or reverse an existing broker
    # holding. That is a separate close/rebalance decision.
    if current_weight * sign < -1e-10:
        raise ValueError('An opposite position already exists. Review its closure before opening this call.')
    delta_weight = target_weight - current_weight
    if delta_weight * sign <= 1e-10:
        raise ValueError('The existing position already meets or exceeds this call’s risk allocation.')
    historical = matrix.to_numpy(dtype=float) @ proposed
    tail = historical[historical <= np.quantile(historical, .05)]
    return {
        'method': 'ledoit_wolf_volatility_budget_v1', 'policy': asdict(policy),
        'symbol': symbol, 'stance': stance, 'price': price, 'equity': equity,
        'cash': cash, 'buying_power': buying_power, 'portfolio_weights': dict(weights),
        'current_weight_pct': current_weight * 100, 'target_weight_pct': target_weight * 100,
        'suggested_notional': abs(delta_weight) * equity,
        'suggested_quantity': abs(delta_weight) * equity / price,
        'asset_volatility_pct': sigma * 100, 'portfolio_volatility_before_pct': before * 100,
        'portfolio_volatility_after_pct': after * 100, 'binding_limit': min(caps, key=caps.get),
        'historical_daily_expected_shortfall_pct': -float(tail.mean()) * 100,
        'observations': len(matrix), 'history_start': str(matrix.index[0]), 'history_end': str(matrix.index[-1]),
        'covariance_shrinkage': float(estimator.shrinkage_),
        'symbols': symbols, 'covariance': covariance.tolist(),
    }


def measure_position_change(*, returns: pd.DataFrame, weights: dict[str,float], symbol: str,
                            delta_weight: float, policy: RiskPolicy) -> dict:
    """Estimate the risk of an exact reduction/closure, including loss of a hedge."""
    policy.validate()
    symbols=sorted({symbol,*weights})
    if any(key not in returns for key in symbols):
        raise ValueError('Risk history is missing for a position.')
    matrix=returns[symbols].replace([np.inf,-np.inf],np.nan).dropna().tail(252)
    if len(matrix)<126:
        raise ValueError('Risk measurement requires 126 common daily returns.')
    estimator=LedoitWolf().fit(matrix.to_numpy(dtype=float))
    covariance=estimator.covariance_*252
    before=np.array([weights.get(key,0) for key in symbols],dtype=float)
    after=before.copy();after[symbols.index(symbol)]+=delta_weight
    return {'method':'ledoit_wolf_volatility_budget_v1','policy':asdict(policy),
        'portfolio_volatility_before_pct':float(np.sqrt(max(0,before@covariance@before)))*100,
        'portfolio_volatility_after_pct':float(np.sqrt(max(0,after@covariance@after)))*100,
        'gross_after_pct':float(np.abs(after).sum())*100,
        'observations':len(matrix),'history_start':str(matrix.index[0]),'history_end':str(matrix.index[-1]),
        'covariance_shrinkage':float(estimator.shrinkage_)}
