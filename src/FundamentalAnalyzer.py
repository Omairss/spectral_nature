import numpy as np
import pandas as pd
import os
import getpass
import argparse
import yfinance as yf
import plotly.express as px
import time
import datetime
import pickle
from plotly.subplots import make_subplots
from plotly.graph_objs import Figure
import plotly.graph_objects as go
from difflib import get_close_matches


# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'

FUNDAMENTAL_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'common', 'stock_fundamental')
print(os.listdir(DATA_STORE_CORE))

def find_closest_index_item(df, item_name, cutoff=0.6):
    """Returns None for 'Issuance Of Debt' so it's not treated as an outflow."""
    if df.empty:
        return None
    if item_name.lower() == "issuance of debt":
        return None
    index_list = df.index.tolist()
    close_matches = get_close_matches(item_name, index_list, n=1, cutoff=cutoff)
    return close_matches[0] if close_matches else None


class Financials():
    def __init__(self, ticker, data_path=None, data=None):
        self.ticker = ticker
        self.data_path = FUNDAMENTAL_HISTORY_STORE
        self.income_statement = None
        self.balance_sheet = None
        self.cash_flow = None
        self.figs = []
        self.cooldown_s = 3  # Set the cooldown period in seconds

    def get_financials(self):
        print(f"Getting financials for {self.ticker}")
        time.sleep(self.cooldown_s)
        ticker = yf.Ticker(self.ticker)
        self.income_statement = ticker.financials
        self.balance_sheet = ticker.balance_sheet
        self.cash_flow = ticker.cashflow
        return self.income_statement, self.balance_sheet, self.cash_flow

    def create_sankey_diagram(self):
        
        # One way to avoid the KeyError is to look up the correct row names first using the helper function:
        idx_ocf = find_closest_index_item(self.cash_flow, "Total Cash From Operating Activities")
        idx_capex = find_closest_index_item(self.cash_flow, "Capital Expenditures")

        if idx_ocf is None or idx_capex is None:
            print("Could not find the required rows for OCF or CapEx.")
        else:
            fcf = self.cash_flow.loc[idx_ocf] + self.cash_flow.loc[idx_capex]
            print("Free Cash Flow (by column):")
            print(fcf)
        
        
        items_map = {
            "Operating Cash Flow": "Total Cash From Operating Activities",
            "Repurchase Of Capital Stock": "Repurchase Of Stock",
            "Repayment Of Debt": "Repayment Of Debt",
            "Issuance Of Debt": "Issuance Of Debt",
            "Capital Expenditure": "Capital Expenditures",
            "Interest Paid": "Interest Paid",
            "Income Tax Paid": "Income Tax Paid",
        }
        outflow_items = [
            "Repurchase Of Capital Stock",
            "Repayment Of Debt",
            "Issuance Of Debt",
            "Capital Expenditure",
            "Interest Paid",
            "Income Tax Paid",
        ]
        node_list = list(items_map.keys())
        node_list.append("Free Cash Flow (Remainder)")
        node_list.append("Free Cash Flow")

        indices_map = {}
        for conceptual_name, raw_item_name in items_map.items():
            idx_match = find_closest_index_item(self.cash_flow, raw_item_name) if raw_item_name else None
            indices_map[conceptual_name] = idx_match

        cols = self.cash_flow.columns.tolist()
        if not cols:
            raise ValueError("No columns in the cash flow data.")
        latest_col = cols[0]

        ocf_idx = indices_map["Operating Cash Flow"]
        if ocf_idx is None:
            raise ValueError("Could not find 'Operating Cash Flow' in the DataFrame.")
        ocf_value = self.cash_flow.loc[ocf_idx, latest_col]
        if not isinstance(ocf_value, (int, float)) or (ocf_value != ocf_value):
            ocf_value = 0

        outflows_raw = {}
        for item in outflow_items:
            idx_match = indices_map[item]
            val = 0
            if idx_match is not None:
                raw_val = self.cash_flow.loc[idx_match, latest_col]
                if isinstance(raw_val, (int, float)) and (raw_val == raw_val):
                    val = raw_val
            outflows_raw[item] = val

        sum_abs_outflows = sum(abs(v) for v in outflows_raw.values())

        links = []
        node_idx_map = {n: i for i, n in enumerate(node_list)}
        for item in outflow_items:
            source_idx = node_idx_map["Operating Cash Flow"]
            target_idx = node_idx_map[item]
            raw_val = outflows_raw[item]
            fraction = abs(raw_val) / sum_abs_outflows if sum_abs_outflows else 0
            scaled_val = abs(ocf_value) * fraction
            links.append(dict(source=source_idx, target=target_idx, value=scaled_val))

        total_scaled_outflows = sum(link["value"] for link in links)
        leftover = abs(ocf_value) - total_scaled_outflows
        if leftover < 0:
            leftover = 0
        links.append(dict(
            source=node_idx_map["Operating Cash Flow"],
            target=node_idx_map["Free Cash Flow (Remainder)"],
            value=leftover
        ))

        links.append(dict(
            source=node_idx_map["Operating Cash Flow"],
            target=node_idx_map["Free Cash Flow"],
            value=fcf[latest_col]
        ))

        node_hover_data = []
        for node in node_list:
            line_item = indices_map.get(node)
            if line_item is None:
                node_hover_data.append("Conceptual/No direct line item")
            else:
                node_hover_data.append(line_item)

        fig = go.Figure(data=[go.Sankey(
            node=dict(
                pad=15,
                thickness=20,
                line=dict(color="black", width=0.5),
                label=node_list,
                customdata=node_hover_data,
                hovertemplate=("Node: %{label}<br>"
                            "DataFrame Line Item: %{customdata}<extra></extra>")
            ),
            link=dict(
                source=[lnk["source"] for lnk in links],
                target=[lnk["target"] for lnk in links],
                value=[lnk["value"] for lnk in links],
                hovertemplate="Value: %{value}<extra></extra>"
            )
        )])
        fig.update_layout(title_text="Cash Flow Statement Sankey Diagram (with Remainder and FCF)", font_size=10)
        self.figs.append(fig)
        return

    def generate_fundamental_timechart(self):
        # Transpose the income statement for better visualization
        income_statement_transposed = self.income_statement.T

        # Reset the index to have dates as a column
        income_statement_transposed.reset_index(inplace=True)

        # Melt the dataframe to have a long format suitable for plotly express
        income_statement_melted = income_statement_transposed.melt(id_vars="index", var_name="Account", value_name="Amount")

        # Create a line chart for the income statement
        fig_income_statement = px.line(income_statement_melted, 
                                       x="index", 
                                       y="Amount", 
                                       color="Account", 
                                       title="Income Statement",
                                       labels={"index": "Date", "Amount": "Amount"})

        fig_income_statement.update_layout(xaxis_title="Date", yaxis_title="Amount")

        self.figs.append(fig_income_statement)
        return

def main(ticker: str, cache_mode: str):
    """
    Main function to retrieve and cache financial data for a given ticker.
    Args:
        ticker (str): Stock ticker symbol.
        cache_mode (str): Cache mode, can be 'local', 'refresh', or other modes.
    Returns:
        dict: A dictionary containing the financial data plotly figure and DataFrame.
    """

    # Check if the technical history store directory exists, if not, create it
    if not os.path.exists(FUNDAMENTAL_HISTORY_STORE):
        os.makedirs(FUNDAMENTAL_HISTORY_STORE)

    # Define the file path for caching
    current_date_str = datetime.datetime.now().strftime('%Y%m%d')
    cache_file_path = os.path.join(FUNDAMENTAL_HISTORY_STORE, f"{ticker}_fundamental_data_{current_date_str}.pkl")
    
    # Function to check if the cache file is older than 3 hours
    def is_cache_stale(file_path):
        if not os.path.exists(file_path):
            return True
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600

    # Check cache mode and load from cache if applicable
    if cache_mode == 'local' and os.path.exists(cache_file_path) and not is_cache_stale(cache_file_path):
        print("Loading financial data from local cache...")
        print(cache_file_path)
        with open(cache_file_path, 'rb') as f:
            financial_bundle = pickle.load(f)
        return financial_bundle

    # Fetch new data
    financials = Financials(ticker)
    financials.get_financials()
    financials.generate_fundamental_timechart()
    financials.create_sankey_diagram()

    financial_bundle = {
        'income_statement': financials.income_statement,
        'balance_sheet': financials.balance_sheet,
        'cash_flow': financials.cash_flow,
        'figs': financials.figs
    }

    # Save the new data to the cache file if needed
    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing financial data...")
        with open(cache_file_path, 'wb') as f:
            pickle.dump(financial_bundle, f)

    return financial_bundle

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Financial Data Analysis')
    parser.add_argument('--ticker', required=True, help='Stock ticker symbol')
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the financial data')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache')

    args = parser.parse_args()
    
    ticker = args.ticker
    force_refresh = args.force_refresh
    force_local = args.force_local

    if force_refresh:
        cache_mode = "refresh"
    elif not force_refresh:
        cache_mode = "normal"
        if force_local:
            cache_mode = "local"
    
    if force_refresh and force_local:
        print("Error: Cannot force refresh and use local cache at the same time.")
        exit(1)

    main(ticker, cache_mode)
