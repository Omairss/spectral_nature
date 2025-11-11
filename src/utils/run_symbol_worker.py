import os
import sys
import time
import pickle as pkl
from datetime import datetime
from typing import Dict, Any

import pandas as pd


def log_progress(progress_log_path: str, message: str) -> None:
    """Append a timestamped message to the shared progress log file."""
    try:
        timestamp = datetime.utcnow().isoformat()
        with open(progress_log_path, 'a') as f:
            f.write(f'[{timestamp}] {message}\n')
    except Exception:
        # Avoid crashing workers if logging fails for any reason
        pass


def run_symbol(symbol: str, repo_path: str, progress_log_path: str) -> Dict[str, Any]:
    """
    Worker function executed in a separate process for each symbol.

    Parameters
    - symbol: Ticker symbol to process
    - repo_path: Absolute path to repo so the worker can import project modules
    - progress_log_path: File path for appending progress logs

    Returns
    - summary dict with per-symbol metrics
    """
    # Ensure per-process/module cache isolation to avoid cross-talk
    sys.path.append(repo_path)

    start_time = time.time()
    log_progress(progress_log_path, f'START symbol={symbol}')

    # Define cache path for this worker
    cache_path = os.path.join(repo_path, 'cache')

    # Explicit imports (avoid wildcard import inside function)
    from src.utils.alpaca_utils import (
        get_historical_stock_price,
        generate_option_symbols,
        get_historical_options_data_cached_fast,
        filter_option_trades_by_ff,
        get_all_open_positions_value,
        check_existing_positions,
        execute_identified_trades,
    )
    import src.utils.alpaca_utils as alpaca_utils

    # reset module-level in-memory cache for this worker
    alpaca_utils.options_cache_memory = {}

    with open(os.path.join(cache_path, 'headers.pkl'), 'rb') as file:
        headers, paper_header = pkl.load(file)

    timeframe = '1D'
    historical_start_date = '2024-01-01'
    historical_end_date = 'now'
    initial_capital = 100000000  # Starting with $100,000
    max_position_ratio_of_capital = 1  # Max 10% of cash per trade
    max_number_of_open_positions = 20000  # Maximum number of open positions

    # Get historical stock price data
    c, h, l, t = get_historical_stock_price(
        symbol=symbol,
        timeframe=timeframe,
        historical_start_date=historical_start_date,
        historical_end_date=historical_end_date,
        headers=headers,
    )
    stock_data = pd.DataFrame([c, h, l, t]).T
    stock_data.columns = ['Mid', 'High', 'Low', 'Date']
    stock_data['Date'] = pd.to_datetime(stock_data['Date'])

    options_price_tracker = {}
    options_seen = set()
    open_positions = {}
    closed_positions = []
    trade_id_counter = 0
    portfolio_history = []
    current_cash = initial_capital * max_position_ratio_of_capital
    total_capital_deployed = 0
    positions_history = []

    # Iterate over each day with manual progress increments
    total_days = len(t)
    for idx, (high, low, mid, current_date) in enumerate(list(zip(h, l, c, t))):

        try:
            if idx % 25 == 0:
                pct = (idx / max(total_days, 1)) * 100
                log_progress(
                    progress_log_path,
                    f'symbol={symbol} day_index={idx}/{total_days} ({pct:0.2f}%) current_date={current_date}',
                )

            max_strike = high + (0.05 * high)
            min_strike = low - (0.05 * low)

            put_option_symbols = generate_option_symbols(
                symbol=symbol,
                current_date=current_date,
                end_date_delta=(25, 120),
                min_strike=min_strike,
                max_strike=low,
                strike_increment=1,
                op_type='put',
            )

            call_option_symbols = generate_option_symbols(
                symbol=symbol,
                current_date=current_date,
                end_date_delta=(25, 120),
                min_strike=high,
                max_strike=max_strike,
                strike_increment=1,
                op_type='call',
            )

            put_option_symbols = [s for s in put_option_symbols if s not in options_seen]
            call_option_symbols = [s for s in call_option_symbols if s not in options_seen]

            put_options_price_tracker = get_historical_options_data_cached_fast(
                option_symbols=put_option_symbols,
                current_date=current_date,
                stock_data=stock_data,
                timeframe='1D',
                headers=headers,
            )

            call_options_price_tracker = get_historical_options_data_cached_fast(
                option_symbols=call_option_symbols,
                current_date=current_date,
                stock_data=stock_data,
                timeframe='1D',
                headers=headers,
            )

            options_seen = options_seen.union(set(put_option_symbols))
            options_seen = options_seen.union(set(call_option_symbols))

            for key in sorted(put_options_price_tracker.keys()):
                if key not in options_price_tracker:
                    options_price_tracker[key] = put_options_price_tracker[key]
                else:
                    options_price_tracker[key] = pd.concat(
                        [options_price_tracker[key], put_options_price_tracker[key]]
                    )

            for key in sorted(call_options_price_tracker.keys()):
                if key not in options_price_tracker:
                    options_price_tracker[key] = call_options_price_tracker[key]
                else:
                    options_price_tracker[key] = pd.concat(
                        [options_price_tracker[key], call_options_price_tracker[key]]
                    )

            concatenated_options = pd.concat(options_price_tracker.values())
            concatenated_options = concatenated_options[
                ((concatenated_options['Type'] == 'call') &
                (concatenated_options['Strike Price'] > concatenated_options['Current Stock Price']))
                |
                ((concatenated_options['Type'] == 'put') &
                (concatenated_options['Strike Price'] < concatenated_options['Current Stock Price']))
            ]
            current_options = concatenated_options[
                concatenated_options['Current Date'].dt.date == pd.to_datetime(current_date).date()
            ]
            strike_group = {
                int(key): group for key, group in current_options.groupby('Strike Price', sort=True)
            }

            list_of_options_dataframes = filter_option_trades_by_ff(
                strike_group=strike_group,
                current_price=mid,
                min_time_distance=20,
                max_time_distance=120,
                min_ff_perc=10,
                max_ff_perc=200,
                current_date=current_date,
                print_output=False,
            )
            list_of_options_dataframes = sorted(
                list_of_options_dataframes,
                key=lambda x: x['Forward Factor'][0],
                reverse=True,
            )

            open_positions_value = get_all_open_positions_value(open_positions)
            current_portfolio_value = current_cash + open_positions_value

            portfolio_history, open_positions, closed_positions, current_cash = check_existing_positions(
                portfolio_history,
                open_positions,
                closed_positions,
                current_date,
                current_cash,
                options_price_tracker,
                short_exit_limits=(-200, 200),
                long_exit_limits=(-200, 200),
                position_exit_limits=(-200, 200),
                stock_data=stock_data,
            )

            open_positions, num_trades_executed_today, current_cash, trade_id_counter = execute_identified_trades(
                list_of_options_dataframes=list_of_options_dataframes,
                open_positions=open_positions,
                current_date=current_date,
                current_cash=current_cash,
                trade_id_counter=trade_id_counter,
                total_capital_deployed=total_capital_deployed,
                max_number_of_open_positions=max_number_of_open_positions,
                stock_strike_deviation_limit=5,
                max_position_ratio_of_capital_per_trade=0.01,
                num_contracts_limit=1,
            )
            closed_trades_df = pd.DataFrame(closed_positions)

            with open(os.path.join(cache_path, f'{symbol}_closed_trades.pkl'), 'wb') as file:
                pkl.dump(closed_trades_df, file)
        except:
            continue

    closed_trades_df = pd.DataFrame(closed_positions)
    portfolio_df = pd.DataFrame(portfolio_history)

    try:
        summary = {'symbol': symbol, 'total_trades': int(len(closed_trades_df or []))}
        if len(closed_trades_df) > 0:
            closed_trades_df['return_pct'] = (
                closed_trades_df['final_pnl'] / closed_trades_df['capital_deployed']
            ) * 100
            with open(os.path.join(cache_path, f'{symbol}_closed_trades.pkl'), 'wb') as file:
                pkl.dump(closed_trades_df, file)
            summary.update({
                'total_pnl': float(closed_trades_df['final_pnl'].sum()),
                'win_rate': float(((closed_trades_df['final_pnl'] > 0).sum() / len(closed_trades_df)) * 100),
                'avg_pnl': float(closed_trades_df['final_pnl'].mean()),
            })
    except:
        summary = {'symbol': symbol, 'total_trades': 0}
    elapsed = time.time() - start_time
    log_progress(progress_log_path, f'END symbol={symbol} elapsed_sec={elapsed:0.2f}')
    return summary
