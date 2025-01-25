import numpy as np



def calculate_sharpe_ratio(returns, risk_free_rate=0.01):
    excess_returns = returns - risk_free_rate
    return np.mean(excess_returns) / np.std(excess_returns)

def calculate_alpha(portfolio_returns, benchmark_returns, risk_free_rate=0.01):
    excess_portfolio_returns = portfolio_returns - risk_free_rate
    excess_benchmark_returns = benchmark_returns - risk_free_rate
    return np.mean(excess_portfolio_returns - excess_benchmark_returns)

def calculate_max_drawdown(returns):
    cumulative_returns = (1 + returns).cumprod()
    peak = cumulative_returns.cummax()
    drawdown = (cumulative_returns - peak) / peak
    return drawdown.min()

def calculate_standard_deviation(returns):
    return np.std(returns)

def calculate_beta(portfolio_returns, benchmark_returns):
    covariance_matrix = np.cov(portfolio_returns, benchmark_returns)
    beta = covariance_matrix[0, 1] / covariance_matrix[1, 1]
    return beta

def calculate_sortino_ratio(returns, risk_free_rate=0.01):
    excess_returns = returns - risk_free_rate
    downside_returns = excess_returns[excess_returns < 0]
    return np.mean(excess_returns) / np.std(downside_returns)

def calculate_treynor_ratio(returns, benchmark_returns, risk_free_rate=0.01):
    excess_returns = returns - risk_free_rate
    beta = calculate_beta(returns, benchmark_returns)
    return np.mean(excess_returns) / beta

def calculate_information_ratio(portfolio_returns, benchmark_returns):
    active_return = portfolio_returns - benchmark_returns
    tracking_error = np.std(active_return)
    return np.mean(active_return) / tracking_error

