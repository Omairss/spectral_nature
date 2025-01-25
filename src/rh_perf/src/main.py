from data.data_loader import login, get_portfolio_data, get_historical_data
from analysis import performance_metrics
from analysis.comparison import Comparison

def main():
    username = 'your_username'
    password = 'your_password'
    login(username, password)

    portfolio_data = get_portfolio_data()
    index_data = get_historical_data('SPY')  # S&P 500 ETF as an example

    comparison = Comparison(portfolio_data, index_data)

    # Calculate metrics
    sharpe_ratio = performance_metrics.calculate_sharpe_ratio(portfolio_data['returns'])
    alpha = performance_metrics.calculate_alpha(portfolio_data['returns'], index_data['returns'])
    max_drawdown = performance_metrics.calculate_max_drawdown(portfolio_data['returns'])

    # Compare to indices and ETFs
    index_comparison = comparison.compare_to_index()
    etf_comparison = comparison.compare_to_etf()

    # Output results
    print("Portfolio Performance Metrics:")
    print(f"Sharpe Ratio: {sharpe_ratio}")
    print(f"Alpha: {alpha}")
    print(f"Max Drawdown: {max_drawdown}")
    
    print("\nComparison to Indices:")
    print(index_comparison)
    
    print("\nComparison to ETFs:")
    print(etf_comparison)

if __name__ == "__main__":
    main()