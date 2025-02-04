def align_dates(portfolio_data, index_data):
    # Aligns the dates of the portfolio data with the index data
    return portfolio_data[portfolio_data.index.isin(index_data.index)]

def normalize_data(data):
    # Normalizes the data to a range of 0 to 1
    return (data - data.min()) / (data.max() - data.min())

def calculate_daily_returns(data):
    # Calculates daily returns from price data
    return data.pct_change().dropna()