import time
import math
import json 
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import scipy.optimize as opt

from tqdm import tqdm
from scipy.optimize import brentq
from scipy.stats import norm
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Define power law in log-log space
def power_law_log(log_x, alpha, logC):
    return (-alpha * log_x) + logC

def forward_ratio(dte_1, dte_2, iv_1, iv_2):
    
    try:
        T1 = dte_1 / 365.0
        T2 = dte_2 / 365.0
        s_1 = iv_1 
        s_2 = iv_2 

        tv_1 = (s_1 ** 2) * T1
        tv_2 = (s_2 ** 2) * T2

        denom = T2 - T1
        fwd_var = (tv_2 - tv_1) / denom
        fwd_sigma = math.sqrt(fwd_var) 
        ff_ratio = (s_1 - fwd_sigma) / fwd_sigma
    except:
        ff_ratio = 0
    
    return ff_ratio * 100

def get_current_value(symbol, headers):
    
    url = f"https://data.alpaca.markets/v2/stocks/bars/latest?symbols={symbol}&feed=iex"
    data = json.loads(requests.get(url, headers = headers).text)
    current_price = data['bars'][symbol]['c']
    
    return float(current_price), data['bars'][symbol]['t']

def get_options_information(symbol, start_day_offset, end_day_offset, option_type, percentage_to_search, headers):
    
    current_price, current_time = get_current_value(symbol, headers)
    print (f"Symbol | {symbol}\nCurrent Price: {current_price} (Time : {current_time})")

    
    # Days To Expiry
    now = datetime.now(tz = ZoneInfo("America/New_York"))
    start_date = now + timedelta(days = start_day_offset)
    end_date = now + timedelta(days = end_day_offset)

    # Set Expiration Dates
    expiration_date_gte = start_date
    expiration_date_lte = end_date

    # Options Type and Strike Price 
    if option_type == 'put':
        strike_price_gte = current_price - (current_price * percentage_to_search)
        strike_price_lte = current_price

    elif option_type == 'call':
        strike_price_gte = current_price
        strike_price_lte = current_price + (current_price * percentage_to_search)

    # Run API Call
    limit = 1000
    url = f"https://api.alpaca.markets/v2/options/contracts?underlying_symbols={symbol}&limit={limit}&status=active&expiration_date_gte={expiration_date_gte.date()}&expiration_date_lte={expiration_date_lte.date()}&root_symbol={symbol}&type={option_type}&style=american&strike_price_gte={strike_price_gte}&strike_price_lte={strike_price_lte}"
    response = requests.get(url, headers = headers)
    data = json.loads(response.text)

    d = np.zeros((len(data['option_contracts']), 5), dtype = object)
    use_idx = []
    for idx, option in enumerate(data['option_contracts']):
        if option['close_price'] is not None:
            d[idx][0] = option['symbol']
            d[idx][1] = option['expiration_date']
            d[idx][2] = option['strike_price']
            d[idx][3] = option['close_price']
            d[idx][4] = option['type'] 
            use_idx.append(idx)

    data = pd.DataFrame(d[use_idx, :])
    data.columns = ['Symbol', 'Expiration Date', 'Strike Price', 'Close Price', 'Type']

    # A different API call is needed for Greeks and IV
    # Get additional data for each option 
    if len(data['Symbol']) <= 100:
        option_symbol = ",".join(data['Symbol'])
        url = f"https://data.alpaca.markets/v1beta1/options/snapshots?symbols={option_symbol}&feed=indicative&limit=100"
        response = requests.get(url, headers = headers)
        options_data = json.loads(response.text)
    else: 
        options_data = {'snapshots' : {}}
        for batch in np.array_split(data['Symbol'].values, 1 + (len(data['Symbol']) // 100)):
            option_symbol = ",".join(batch)
            url = f"https://data.alpaca.markets/v1beta1/options/snapshots?symbols={option_symbol}&feed=indicative&limit=100"
            response = requests.get(url, headers = headers)
            options_data['snapshots'] = options_data['snapshots'] | json.loads(response.text)['snapshots'] 

    # Clean it all up and put into dataframe 
    d = np.zeros((len(options_data['snapshots']), 13), dtype = object)
    for idx, option in enumerate(options_data['snapshots']):
        d[idx][0] = option

        other_data = data[data['Symbol'] == option].values[0]
        d[idx][1] = other_data[1]
        d[idx][2] = other_data[2]
        d[idx][3] = other_data[3]
        d[idx][4] = other_data[4]

        d[idx][5] = options_data['snapshots'][option]['greeks']['delta']
        d[idx][6] = options_data['snapshots'][option]['greeks']['gamma']
        d[idx][7] = options_data['snapshots'][option]['greeks']['rho']
        d[idx][8] = options_data['snapshots'][option]['greeks']['theta']
        d[idx][9] = options_data['snapshots'][option]['greeks']['vega']
        d[idx][10] = options_data['snapshots'][option]['impliedVolatility']
        d[idx][11] = options_data['snapshots'][option]['latestQuote']['ap']
        d[idx][12] = options_data['snapshots'][option]['latestQuote']['bp']
    option_details = pd.DataFrame(d)
    option_details.columns = ['Symbol', 'Expiration Date', 'Strike Price', 'Close Price', 'Type', 'Delta', 'Gamma', 'Rho', 'Theta', 'Vega', 'IV', 'Buy Price', 'Sell Price']
    option_details = option_details.sort_values(by = ['Strike Price', 'Expiration Date']).reset_index(drop = True)
    strike_group = {int(key): group for key, group in option_details.groupby('Strike Price')}

    return option_details, strike_group, current_price

def filter_option_trades_by_ff(current_price, strike_group, min_time_distance, max_time_distance, min_ff_perc, current_date = 'now', print_output = True):
    
    return_list = []
    # Print out all calendar spreads with a forward factor >= 20%
    for key in strike_group:
        
        if print_output:
            print (f"############################################\n####### Strike Price (Current Price) #######\n############## {key} ({current_price}) ################\n############################################\n")
        df = strike_group[key]
        
        if current_date == 'now':
            now = datetime.now()
        else: 
            now = pd.to_datetime(current_date, utc = True)

        col_names = list(df.columns)
        exp_idx = col_names.index('Expiration Date')
        iv_idx = col_names.index('IV')
        
        try:
            sell_idx = col_names.index('Sell Price')
            buy_idx = col_names.index('Buy Price')
        except:
            sell_idx = col_names.index('Low Option Price')
            buy_idx = col_names.index('High Option Price')
        
        for idx in range(df.shape[0]):

            out_list = []

            dte_1 = (pd.to_datetime(df.values[idx][exp_idx], utc = True) - pd.to_datetime(now, utc = True)).days
            iv_1 = df.values[idx][iv_idx]
            
            out_list.append(df.values[idx])

            for j in range(idx + 1, df.shape[0]):
                dte_2 = (pd.to_datetime(df.values[j][exp_idx], utc = True) - pd.to_datetime(now, utc = True)).days
                iv_2 = df.values[j][iv_idx]

                if (dte_2 - dte_1 >= min_time_distance) and (dte_2 - dte_1 <= max_time_distance):
                    f_ratio = forward_ratio(dte_1, dte_2, iv_1, iv_2)

                    if f_ratio >= min_ff_perc:
                        
                        if print_output:
                            print (f"Forward Ratio: {f_ratio:.3f}% | DTE Front (Sell): {dte_1} | DTE Back (Buy): {dte_2}")
                        out_list.append(df.values[j])
                        
                        net_position = out_list[0][sell_idx] - out_list[1][buy_idx] # sell price - buy price
                        
                        out_pd = pd.DataFrame(out_list)
                        out_pd.columns = col_names
                        
                        out_pd['Forward Factor'] = [f_ratio, f_ratio]
                        out_pd['Net'] = [net_position, net_position]
                        
                        if print_output:
                            display(out_pd)
                        return_list.append(out_pd)
                        out_list = out_list[:-1]
                        
    return return_list



def get_historical_magnitude_plots(symbol, timeframe, historical_start_date, historical_end_date = 'now', day_offsets = [30, 60, 90, 180]):
    
    
    if historical_end_date == 'now':
        now = datetime.now()
        historical_end_date = str(now.date())
    c = []
    h = []
    l = []
    t = []

    url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc"
    response = requests.get(url, headers = headers)

    data = json.loads(response.text)
    for idx in range(len(data['bars'][symbol])):
        c.append(data['bars'][symbol][idx]['c'])
        h.append(data['bars'][symbol][idx]['h'])    
        l.append(data['bars'][symbol][idx]['l'])    
        t.append(data['bars'][symbol][idx]['t'])

    while data['next_page_token'] is not None:
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc&page_token={data['next_page_token']}"
        response = requests.get(url, headers = headers)
        data = json.loads(response.text)
        for idx in range(len(data['bars'][symbol])):
            c.append(data['bars'][symbol][idx]['c'])
            h.append(data['bars'][symbol][idx]['h'])    
            l.append(data['bars'][symbol][idx]['l'])    
            t.append(data['bars'][symbol][idx]['t'])    

    c = np.array(c)
    h = np.array(h)
    l = np.array(l)
    t = np.array([pd.to_datetime(x) for x in t])
    
    plt.figure(figsize = (15, 5))
    plt.title(f"{symbol}: {start_date} - {end_date}")
    plt.plot(t, c)
    plt.xlabel('Closing Price')
    plt.ylabel('Time')
    plt.xticks(rotation = 90)
    plt.show()
    
    for offset in day_offsets:
        now = datetime.now()
        filter_start_date = (now - timedelta(days = offset)).date()
        filter_end_date = str(now.date())

        ###############################
        ########## Histogram ##########
        ###############################

        fig, axs = plt.subplots(2, 2, figsize=(20, 20))

        axs[0][0].set_title(f'Histogram of Day-to-Day Changes\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')

        filter_start_date_ = pd.to_datetime(filter_start_date, utc = True)
        filter_end_date_ = pd.to_datetime(filter_end_date, utc = True)
        indices = np.where((t >= filter_start_date_) & (t <= filter_end_date_))

        bins = axs[0][0].hist(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1], bins = 100, density = True)
        axs[0][0].set_xlabel('% Change')
        axs[0][0].set_ylabel('Probability Density')

        axs[0][1].set_title(f'Histogram of Day-to-Day Change Magnitudes\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')
        bins = axs[0][1].hist(np.abs(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1]), bins = 100, density = True)
        axs[0][1].set_xlabel('Magnitude of % Change')
        axs[0][1].set_ylabel('Probability Density')

        ###############################
        ######## Power Law Fit ########
        ###############################

        # Create histogram
        counts, bin_edges = np.histogram(np.abs(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1]), bins = 100, density = True)

        # Calculate bin centers (not edges)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        # Remove zero counts to avoid log(0)
        nonzero_mask = counts > 0
        x_data = bin_centers[nonzero_mask]
        y_data = counts[nonzero_mask]

        param, _ = opt.curve_fit(f = power_law_log,
                                 xdata = np.log(x_data),
                                 ydata = np.log(y_data))

        alpha, logC = param

        axs[1][0].set_title(f'Linear Scale Power Law Fit\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')
        axs[1][0].hist(np.abs(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1]), bins = 100, label = 'Magnitude of Change', density = True)
        axs[1][0].plot(x_data, np.exp(logC) * x_data**(-alpha), 'r-', linewidth = 2, label = f'Power Law Fit: $\gamma = {alpha:.3f}\ |\ c = {np.exp(logC):.3f}$')
        axs[1][0].set_xlabel('Magnitude of % Change')
        axs[1][0].set_ylabel('Probability Density')
        axs[1][0].legend()

        axs[1][1].set_title(f'Log Scale Power Law Fit\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')
        axs[1][1].loglog(x_data, y_data, 'o', label = 'Magnitude of Change')
        axs[1][1].loglog(x_data, np.exp(logC) * x_data ** (-alpha), 'r-', linewidth = 2, label = f'Power Law Fit: $\gamma = {alpha:.3f}\ |\ c = {np.exp(logC):.3f}$')
        axs[1][1].set_xlabel('Magnitude of % Change')
        axs[1][1].set_ylabel('Probability Density')
        axs[1][1].legend()

        plt.show()
        
        
def get_prediction_error(symbol, timeframe, train_start_date, train_end_date, test_start_date, test_end_date):

    historical_start_date = train_start_date
    historical_end_date = test_end_date

    if historical_end_date == 'now':
        now = datetime.now()
        historical_end_date = str(now.date())
        test_end_date = str(now.date())
    c = []
    h = []
    l = []
    t = []

    url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc"
    response = requests.get(url, headers = headers)

    data = json.loads(response.text)
    for idx in range(len(data['bars'][symbol])):
        c.append(data['bars'][symbol][idx]['c'])
        h.append(data['bars'][symbol][idx]['h'])    
        l.append(data['bars'][symbol][idx]['l'])    
        t.append(data['bars'][symbol][idx]['t'])

    while data['next_page_token'] is not None:
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc&page_token={data['next_page_token']}"
        response = requests.get(url, headers = headers)
        data = json.loads(response.text)
        for idx in range(len(data['bars'][symbol])):
            c.append(data['bars'][symbol][idx]['c'])
            h.append(data['bars'][symbol][idx]['h'])    
            l.append(data['bars'][symbol][idx]['l'])    
            t.append(data['bars'][symbol][idx]['t'])    

    c = np.array(c)
    h = np.array(h)
    l = np.array(l)
    t = np.array([pd.to_datetime(x) for x in t])

    plt.figure(figsize = (15, 5))
    plt.title(f"{symbol}: {start_date} - {end_date}")
    plt.plot(t, c)
    plt.xlabel('Closing Price')
    plt.ylabel('Time')
    plt.xticks(rotation = 90)
    plt.show()
    
    train_start = np.where(t > pd.to_datetime(train_start_date, utc = True))[0][0]
    train_end = np.where(t > pd.to_datetime(train_end_date, utc = True))[0][0]
    test_start = np.where(t > pd.to_datetime(test_start_date, utc = True))[0][0]
    test_end = np.where(t > pd.to_datetime(test_end_date, utc = True))[0][-1]

    train = c[train_start:train_end]
    possible_changes = train[1:] - train[:-1]
    sampled_changes = np.random.choice(possible_changes, test_end - test_start)


    predicted_matrix = []
    gaussian_matrix = []

    difference = []

    for _ in range(100):
        current_value = train[-1]
        predicted_values = [current_value]
        gaussian_values = [current_value]
        for idx in range(len(sampled_changes)):
            predicted_values.append(predicted_values[-1] + sampled_changes[idx])
            gaussian_values.append(gaussian_values[-1] + np.random.normal(loc = np.mean(possible_changes), scale = np.std(possible_changes)))
        predicted_matrix.append(predicted_values)
        gaussian_matrix.append(gaussian_values)

        predicted_error = np.mean(np.square(c[test_start:test_end + 1] - predicted_values))
        gaussian_error = np.mean(np.square(c[test_start:test_end + 1] - gaussian_values))
        difference.append(predicted_error - gaussian_error)


    predicted_values = np.mean(predicted_matrix, axis = 0)
    gaussian_values = np.mean(gaussian_matrix, axis = 0)

    predicted_std = np.std(predicted_matrix, axis = 0)
    gaussian_std = np.std(gaussian_matrix, axis = 0)

    predicted_upper = predicted_values + predicted_std
    predicted_lower = predicted_values - predicted_std

    gaussian_upper = gaussian_values + gaussian_std
    gaussian_lower = gaussian_values - gaussian_std

    predicted_error = np.mean(np.square(c[test_start:test_end + 1] - predicted_values))
    gaussian_error = np.mean(np.square(c[test_start:test_end + 1] - gaussian_values))

    plt.figure(figsize = (15, 5))
    plt.title(f"{symbol}: {test_start_date} - {test_end_date} | Sampled Error - Gaussian Error: ${np.mean(difference):.3f} \pm {np.std(difference):.3f}$")
    plt.plot(t[test_start:], c[test_start:], label = 'True')


    plt.plot(t[test_start:], predicted_values, label = f'Sampled Prediction (MSE = {predicted_error:.3f})')
    plt.fill_between(t[test_start:test_end + 1], predicted_lower, predicted_upper, color = 'red', alpha = 1)

    plt.plot(t[test_start:], gaussian_values, label = f'Gaussian Prediction (MSE = {gaussian_error:.3f})')
    plt.fill_between(t[test_start:test_end + 1], gaussian_lower, gaussian_upper, color = 'green', alpha = 0.1)


    plt.xticks(rotation = 90)
    plt.xlabel('Time')
    plt.ylabel('Price ($)')
    plt.legend()
    plt.show()

    plt.figure(figsize = (15, 5))
    plt.title('Distribution of Errors Between Sampled and Gaussian Predictions')
    plt.hist(difference, bins = 50)
    plt.xlabel('MSE(Sampled) - MSE(Gaussian)')
    plt.ylabel('Frequency')
    plt.show()
    
def get_historical_options_data(option_symbols, current_date, stock_data, timeframe = '1D'):
    
    options_price_tracker = {}
    options_symbols = ",".join(option_symbols)
    start_date = str(current_date.date())
    end_date = current_date + timedelta(30)

    url = f"https://data.alpaca.markets/v1beta1/options/bars?symbols={options_symbols}&timeframe={timeframe}&start={start_date}&limit=1000&sort=asc"
    response = requests.get(url, headers=headers).text
    data = json.loads(response)

    while data['next_page_token'] is not None:
        url = f"https://data.alpaca.markets/v1beta1/options/bars?symbols={options_symbols}&timeframe={timeframe}&start={start_date}&limit=1000&sort=asc&page_token={data['next_page_token']}"
        data = json.loads(requests.get(url, headers=headers).text)

    for op_symbol in data['bars']:    
        df = []
        for idx in range(len(data['bars'][op_symbol])):

            mid_op_price = data['bars'][op_symbol][idx]['c']
            high_op_price = data['bars'][op_symbol][idx]['h']
            low_op_price = data['bars'][op_symbol][idx]['l']
            cur_date = pd.to_datetime(data['bars'][op_symbol][idx]['t'])
            cur_stock_price = stock_data[(stock_data['Date'].dt.date == pd.to_datetime(cur_date, utc = True).date())]['Mid'].values[0]
            strike_price = extract_strike_price_from_symbol(op_symbol)
            exp_date = pd.to_datetime(op_symbol[-15:-9], format = '%y%m%d', utc = True)
            if op_symbol[-9] == 'P':
                op_type = 'put'
            elif op_symbol[-9] == 'C':
                op_type = 'call'
            dte = (exp_date - cur_date).days

            if dte > 0:

                iv = calculate_implied_volatility(option_price = mid_op_price,
                                                  S = cur_stock_price,
                                                  K = strike_price,
                                                  T =  dte / 365,
                                                  r = get_risk_free_rate_estimate(dte),
                                                  option_type = op_type)

                delta = calculate_delta_historical(option_price = mid_op_price,
                                                   strike_price = strike_price,
                                                   expiry = pd.Timestamp(exp_date),
                                                   underlying_price = cur_stock_price,
                                                   risk_free_rate = get_risk_free_rate_estimate(dte),
                                                   option_type = op_type,
                                                   timestamp = pd.Timestamp(cur_date))

                # Add bounds to IV calculation
                if iv is not None and (iv < 0 or iv > 5):  # IV > 500% is suspicious
                    print(f"Unusual IV={iv:.2f} for {op_symbol} on {cur_date}")
                    iv = None  # or skip this data point

                # Delta should be bounded
                if op_type == 'call' and delta is not None and (delta < 0 or delta > 1):
                    print(f"Invalid call delta={delta:.2f} for {op_symbol}")
                    delta = None

                if op_type == 'put' and delta is not None and (delta < -1 or delta > 0):
                    print(f"Invalid put delta={delta:.2f} for {op_symbol}")
                    delta = None


                if iv != 0 and iv is not None and delta is not None:
                    df.append([op_symbol, high_op_price, mid_op_price, low_op_price, cur_date, cur_stock_price, strike_price, exp_date, op_type, iv, delta, dte])


        df = pd.DataFrame(df)
        if df.shape[0] != 0:
            
            df.columns = ['Symbol', 'High Option Price', 'Current Option Price', 'Low Option Price', 'Current Date', 'Current Stock Price', 'Strike Price', 'Expiration Date', 'Type', 'IV', 'Delta', 'DTE']

            options_price_tracker[op_symbol] = df
            options_price_tracker[op_symbol] = options_price_tracker[op_symbol].sort_values(by = 'Current Date').reset_index(drop = True)
        
    return options_price_tracker

def get_risk_free_rate_estimate(days_to_expiration):
            """Simple estimate based on current yield curve"""
            if days_to_expiration <= 30:
                return 0.0393  # 3-month rate
            elif days_to_expiration <= 365:
                return 0.0370  # 1-year rate
            else:
                return 0.0408  # 10-year rate

# Calculate implied volatility
def calculate_implied_volatility(
    option_price: float, S: float, K: float, T: float, r: float, option_type: str
):
    """
    Calculate implied volatility using the Black-Scholes model.

    Args:
        option_price: Market price of the option
        S: Current stock price (underlying asset price)
        K: Strike price of the option
        T: Time to expiration in years
        r: Risk-free interest rate
        option_type: Type of option (ContractType.CALL or ContractType.PUT)

    Returns:
        Implied volatility as a float, or None if calculation fails
    """
    # Define a reasonable range for sigma
    sigma_lower = 1e-6
    sigma_upper = 5.0  # Adjust upper limit if necessary

    # Check if the option is out-of-the-money and price is close to zero
    intrinsic_value = max(0, (S - K) if option_type == "call" else (K - S))
    if option_price <= intrinsic_value + 1e-6:
        # print("Option price is close to intrinsic value; implied volatility is near zero.") # Uncomment for checking the status
        return 0.0

    # Define the function to find the root
    def option_price_diff(sigma: float) -> float:
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type == "call":
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        elif option_type == "put":
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        return price - option_price

    try:
        return brentq(option_price_diff, sigma_lower, sigma_upper)
    except ValueError as e:
#         print(f"Failed to find implied volatility: {e}")
        return None
    
# Calculate historical option Delta
def calculate_delta_historical(
    option_price: float,
    strike_price: float,
    expiry: pd.Timestamp,
    underlying_price: float,
    risk_free_rate: float,
    option_type: str,
    timestamp: pd.Timestamp,
):
    """
    Calculate option delta using historical data and the Black-Scholes model.

    Args:
        option_price: Market price of the option
        strike_price: Strike price of the option
        expiry: Option expiration datetime
        underlying_price: Current price of underlying asset
        risk_free_rate: Risk-free interest rate
        option_type: Type of option ('call' or 'put')
        timestamp: Current timestamp for calculation

    Returns:
        Option delta as a float, or None if calculation fails
    """
    # Calculate the time to expiry in years
    T = (expiry - timestamp).total_seconds() / (365 * 24 * 60 * 60)
    # Set minimum T to avoid zero
    T = max(T, 1e-6)

    if T == 1e-6:
#         print("Option has expired or is expiring now; setting delta based on intrinsic value.")
        if option_type == "put":
            return -1.0 if underlying_price < strike_price else 0.0
        else:
            return 1.0 if underlying_price > strike_price else 0.0

    implied_volatility = calculate_implied_volatility(
        option_price, underlying_price, strike_price, T, risk_free_rate, option_type
    )
    if implied_volatility is None or implied_volatility <= 1e-6:
        # print("Implied volatility could not be determined, skipping delta calculation.")
        return None

    d1 = (
        np.log(underlying_price / strike_price)
        + (risk_free_rate + 0.5 * implied_volatility**2) * T
    ) / (implied_volatility * np.sqrt(T))
    delta = norm.cdf(d1) if option_type == "call" else -norm.cdf(-d1)
    return delta

def extract_strike_price_from_symbol(symbol: str) -> float:
    """
    Extract strike price from option symbol.
    Converts last 8 digits of option symbol to strike price by dividing by 1000.

    Args:
        symbol: Option symbol (e.g., 'SPY250616P00571000')

    Returns:
        float: Strike price (e.g., 571.0) or 0.0 if invalid format
    """
    # Option symbols typically have format: TICKER + YYMMDD + (C/P) + 8-digit strike price
    # The last 8 digits represent strike price * 1000
    try:
        # Extract the last 8 digits and convert to strike price
        strike_str = symbol[-8:]
        strike_price = float(strike_str) / 1000.0
        return strike_price
    except (ValueError, IndexError):
        print(f"Warning: Could not extract strike price from symbol {symbol}")
        return 0.0