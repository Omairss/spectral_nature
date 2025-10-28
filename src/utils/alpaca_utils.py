import time
import math
import json 
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def forward_ratio(dte_1, dte_2, iv_1, iv_2):

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

def filter_option_trades_by_ff(current_price, strike_group, min_time_distance, min_ff_perc):
    
    # Print out all calendar spreads with a forward factor >= 20%
    for key in strike_group:
        
        print (f"############################################\n####### Strike Price (Current Price) #######\n############## {key} ({current_price}) ################\n############################################\n")
        df = strike_group[key]
        now = datetime.now()

        for idx in range(df.shape[0]):

            out_list = []

            dte_1 = (pd.to_datetime(df.values[idx][1]) - pd.to_datetime(now)).days
            iv_1 = df.values[idx][10]

            out_list.append(df.values[idx])

            for j in range(idx + 1, df.shape[0]):
                dte_2 = (pd.to_datetime(df.values[j][1]) - pd.to_datetime(now)).days
                iv_2 = df.values[j][10]

                if dte_2 - dte_1 >= min_time_distance:
                    f_ratio = forward_ratio(dte_1, dte_2, iv_1, iv_2)

                    if f_ratio >= min_ff_perc:

                        print (f"Forward Ratio: {f_ratio:.3f}% | DTE Front (Sell): {dte_1} | DTE Back (Buy): {dte_2}")
                        out_list.append(df.values[j])
                        out_pd = pd.DataFrame(out_list)
                        out_pd.columns = ['Symbol', 'Expiration Date', 'Strike Price', 'Close Price', 'Type', 'Delta', 'Gamma', 'Rho', 'Theta', 'Vega', 'IV', 'Buy Price', 'Sell Price']
                        display(out_pd)
                        out_list = out_list[:-1]

