import dash
import pandas as pd
from dash import dcc, html, Input, Output, State
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from flask import Flask, redirect, url_for, request, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import plotly.graph_objects as go  
import plotly.express as px
from plotly.subplots import make_subplots
from collections import Counter
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
sys.path.append("..")
import yfinance as yf
import FundamentalAnalyzer, MarketExplorer, TechnicalAnalyzer, OptionFinder, CurrentStatus, LinkedAuth


# ----------------------------------------------------------------
# Set Plotly theme to dark
# ----------------------------------------------------------------
from dash_bootstrap_templates import load_figure_template

# loads the "darkly" template and sets it as the default
load_figure_template("darkly")

# ----------------------------------------------------------------
# Flask app setup
# ----------------------------------------------------------------

app = Flask(__name__, static_folder='assets')
app.secret_key = "das"  # for demonstration

login_manager = LoginManager()
login_manager.init_app(app)
us, ps = LinkedAuth.get_creds("spectral-nature-kvault", retreive = ['rh-username', 'rh-pswd'])

global BENCHMARK_ENTITIES
BENCHMARK_ENTITIES = ['portfolio', 'SPY', 'DIA', 'QQQ', 'VOO', 'BRK.B', 'ARKK']

# Example function to combine multiple figures into subplots
def combine_figures_into_subplots(figures, titles):
    # Create a subplot grid with the appropriate number of rows and columns
    rows = len(figures)
    cols = 1  # Assuming one column for simplicity, adjust as needed

    # Create the subplot figure
    combined_fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles)

    # Add traces from each figure to the appropriate subplot
    for i, fig in enumerate(figures):
        for trace in fig['data']:
            combined_fig.add_trace(trace, row=i+1, col=1)

    # Update layout
    combined_fig.update_layout(
        autosize=True,
        title="Combined Market Charts",
        template="darkly"
    )

    return combined_fig

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# ----------------------------------------------------------------
# 1) Pure HTML Navbar for Flask pages
# ----------------------------------------------------------------
# This returns an HTML string that Flask can inject into templates
def get_flask_navbar(active="Home"):
    # Mark the active link (Home or Dash)
    home_active = "active" if active == "Home" else ""
    dash_active = "active" if active == "Dash" else ""

    navbar_html = f"""
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
      <a class="navbar-brand ms-2" href="/">Torres Capital</a>
      <button class="navbar-toggler" type="button" data-toggle="collapse" 
              data-target="#navbarSupportedContent" aria-controls="navbarSupportedContent" 
              aria-expanded="false" aria-label="Toggle navigation">
        <span class="navbar-toggler-icon"></span>
      </button>

      <div class="collapse navbar-collapse" id="navbarSupportedContent">
        <ul class="navbar-nav ml-auto">
          <li class="nav-item {home_active}">
            <a class="nav-link" href="/">Home</a>
          </li>
          <li class="nav-item {dash_active}">
            <a class="nav-link" href="/dash/">Dash</a>
          </li>
          <li class="nav-item">
            <a class="nav-link" href="/logout">Logout</a>
          </li>
        </ul>
      </div>
    </nav>
    """
    return navbar_html

# ----------------------------------------------------------------
# 2) Dash app setup, including a DBC Navbar for the Dash layout
# ----------------------------------------------------------------

# Dash app is mounted on the Flask server
dash_app = dash.Dash(
    __name__,
    server=app,
    routes_pathname_prefix="/dash/",
    external_stylesheets=[dbc.themes.DARKLY]  # Darkly theme
)


# This is the Dash/dbc-based version of the navbar
def get_dash_navbar():
    return dbc.Navbar(
        dbc.Container([
            dbc.NavbarBrand([
                html.Img(src="assets/logo/rectangle/Color logo - no background 2.svg", height="80px"),
                ""
            ], className="ms-2 text-white"),
            dbc.Nav([
                dbc.NavItem(dbc.NavLink("Home", href="/", className="text-white")),
                dbc.NavItem(dbc.NavLink("Dash", href="/dash/", active=True, className="text-white")),
                dbc.NavItem(dbc.NavLink("Logout", href="/logout", className="text-white"))
            ], navbar=True)
        ]),
        color="dark",
        dark=True,
        className="mb-4"
    )

# Define Dash layout
dash_app.layout = dbc.Container([
    dcc.Location(id="url"),  # for future callbacks if needed
    # Use the Dash version of the navbar
    get_dash_navbar(),
    html.Div([
        #html.H1("", className="text-white mt-4"),
        dcc.Tabs(id="tabs", value='tab1', children=[
            dcc.Tab(label='Past Performance', value='tab0', className="bg-dark text-white"),
            dcc.Tab(label='Current Portfolio', value='tab1', className="bg-dark text-white"),
            dcc.Tab(label='Market Opportunity', value='tab2', className="bg-dark text-white"),
            dcc.Tab(label='Strategizer - Option', value='tab3', className="bg-dark text-white"),
            dcc.Tab(label='Strategizer - Technical', value='tab4', className="bg-dark text-white"),
            dcc.Tab(label='Strategizer - Fundamental', value='tab5', className="bg-dark text-white")
        ]),
        html.Div(id='tabs-content', className="text-white mt-3")
    ])
], fluid=True)

# Callbacks for the Dash app
@dash_app.callback(Output('tabs-content', 'children'), [Input('tabs', 'value')])
def render_content(tab):
    # Past Performance
    if tab == 'tab0':
        return html.Div([
                dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
                dcc.Graph(id="sample-graph")
                ], className="mt-3")
    # Current Portfolio
    if tab == 'tab1':
        return html.Div([
            dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
            dcc.Graph(id="portfolio-timechart"),
            dcc.Graph(id="earnings-barchart"),
            dcc.Graph(id="performance-chart")
        ], className="mt-3")
    # Market Opportunity
    if tab == 'tab2':
        return html.Div([
          dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
          dcc.Graph(id="market-watchlist-icicle"),
          #NEW
          html.Div(id="market-icicle-selection", className="mt-2"),
          dcc.Graph(id="market-graph"),
          html.Div(id="news-feed", children=create_news_cards())
          ], className="mt-3")
    # Strategizer - Option
    if tab == 'tab3':
        return html.Div([
            dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
            dbc.Row([
          dbc.Col(dbc.Input(id="ticker-input", placeholder="Enter TICKER", type="text", className="mb-3"), width=6),
          dbc.Col(dbc.Input(id="strike-price", placeholder="Enter Strike Price", type="number", className="mb-3"), width=6)
            ]),
            dcc.Graph(id="option-graph")
        ], className="mt-3")
    # Strategizer - Technical
    if tab == 'tab4':
        return html.Div([
            dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
            dbc.Row([
          dbc.Col(dbc.Input(id="ticker-input", placeholder="Enter TICKER", type="text", className="mb-3"), width=6),
            ]),
            dcc.Graph(id="technical-charts")
        ], className="mt-3")
    # Strategizer - Fundamental
    if tab == 'tab5':
        return html.Div([
          dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
          dbc.Row([
          dbc.Col(dbc.Input(id="ticker-input", placeholder="Enter TICKER", type="text", className="mb-3"), width=6),
            ]),
            dcc.Graph(id="fundamental-timechart"),
            dcc.Graph(id="fundamental-sankey")
        ], className="mt-3")
    else:
        return html.Div("Coming soon", className="mt-3")

@dash_app.callback(Output("option-graph", "figure"), [Input("update-button", "n_clicks")],
                                                       [State("ticker-input", "value"),
                                                       State("strike-price", "value")])
def update_option_chart(n, ticker, strike_price):
  if not ticker:
    return go.Figure()

  option_bundle = OptionFinder.main(us, ps, ticker, float(strike_price), 'refresh')
  (df, fig) = option_bundle['df'], option_bundle['fig']

  fig.update_layout(
    autosize = True,
    title=f"Option Booster for {ticker}",
    template="darkly",
    width=1200,  # Set the width of the figure
    height=1200
  )
  return fig

@dash_app.callback(Output("portfolio-timechart", "figure"), [Input("update-button", "n_clicks")])
def update_portfolio_chart(n):
    current_status = CurrentStatus.main(us, ps, 'historical_w_benchmarks', 'refresh')
    df = pd.DataFrame(current_status['historical_df']).copy()

    print('PORTFOLIO TIMECHART')
    print(df)

    if 'timestamp' not in df.columns:
        return go.Figure(layout=dict(title="historical_df missing 'timestamp'", template="plotly_dark"))

    # Ensure proper datetime and sort
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['timestamp']).sort_values('timestamp')

    # Plot portfolio + available benchmarks
    y_cols = [c for c in ['portfolio', 'SPY', 'DIA', 'QQQ', 'VOO', 'BRK.B', 'ARKK'] if c in df.columns]
    if not y_cols:
        return go.Figure(layout=dict(title="No portfolio/benchmark columns to plot", template="plotly_dark"))

    for c in y_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')

    # Color map: portfolio bright green, benchmarks dull grays
    dull_palette = ['#8c8c8c', '#7a7a7a', '#707070', '#686868', '#606060', '#585858', '#505050']
    color_discrete_map = {}
    if 'portfolio' in y_cols:
        color_discrete_map['portfolio'] = '#00e676'  # bright green
    bench_names = [c for c in y_cols if c != 'portfolio']
    for i, name in enumerate(bench_names):
        color_discrete_map[name] = dull_palette[i % len(dull_palette)]
    fig = px.line(
        df,
        x='timestamp',
        y=y_cols,
        title='Portfolio vs Benchmarks',
        labels={'timestamp': 'Date', 'value': 'Value ($)'},
        color_discrete_map=color_discrete_map
    )

    # Secondary y-axis for portfolio only
    fig.update_layout(
        template='plotly_dark',
        yaxis=dict(title='Benchmarks ($)'),
        yaxis2=dict(title='Portfolio ($)', overlaying='y', side='right', showgrid=False),
        legend_title_text='Series'
    )

    # Style traces: portfolio -> y2, bright/thicker; benchmarks -> dull, thinner, semi-transparent
    def _style_trace(tr):
        if tr.name == 'portfolio':
            tr.update(yaxis='y2', line=dict(width=3), opacity=1.0, marker=dict(size=4))
        else:
            tr.update(line=dict(width=1.2), opacity=0.55, marker=dict(size=2))
        return tr

    fig.for_each_trace(_style_trace)
    fig.update_traces(mode='lines+markers', connectgaps=True)

    return fig


@dash_app.callback(Output("earnings-barchart", "figure"), [Input("update-button", "n_clicks")])
def normalized_earnings_chart(n):
    
  current_status = CurrentStatus.main(us, ps, 'holdings', 'local')
  holdings_df = pd.DataFrame(current_status['holdings_df']).T
  holdings_df['pe_ratio_abs'] = holdings_df['pe_ratio'].apply(lambda x: max(float(x), 1) if x is not None else 1)
  holdings_df['equity_normalized_pe_ratio'] = holdings_df['equity'].astype(float) / holdings_df['pe_ratio'].astype(float)
  holdings_df[['name', 'equity', 'equity_normalized_pe_ratio']].sort_values(by = 'equity_normalized_pe_ratio', ascending = False)
  # Select the columns to plot
  columns_to_plot = ['equity', 'equity_normalized_pe_ratio']

  # Melt the dataframe to long format
  holdings_melted = holdings_df[['name'] + columns_to_plot].melt(id_vars='name', value_vars=columns_to_plot, var_name='Metric', value_name='Value')
  holdings_melted['Value'] = holdings_melted['Value'].astype(float)
  
  #sort
  holdings_melted = holdings_melted.sort_values(by=['Metric', 'Value'], ascending=[True, False])

  # Create the grouped bar chart
  fig = px.bar(holdings_melted, x='name', y='Value', color='Metric', barmode='group', title='Holdings Equity and Equity Normalized PE Ratio', log_y=True)
  return fig

@dash_app.callback(Output("performance-chart", "figure"), [Input("update-button", "n_clicks")])
def performance_chart(n):

  results = CurrentStatus.main(us, ps, 'performance', 'local')
  performance_df = results['performance_df']

  # Filter ranges
  performance_cum = performance_df[performance_df['Year'] == 'Cumulative']
  performance_df = performance_df[performance_df['Year'] != 2020]
  performance_df = performance_df[performance_df['Year'] != 'Cumulative']


  # Define a color map for entities
  color_map = {
      'portfolio': 'blue',
      'SPY': 'red',
      'DIA': 'green',
      'QQQ': 'purple',
      'VOO': 'orange',
      'BRK.B': 'brown',
      'ARKK': 'pink'
  }

  # Create a subplot figure with 5 rows and 1 column
  fig = make_subplots(rows=5, cols=1, subplot_titles=(
      'Annual Return Over Time',
      'Sharpe Ratio Over Time',
      'Beta (vs SPY) Over Time',
      'Alpha (vs SPY) Over Time',
      'Max Drawdown Over Time'
  ))

  # Annual Return plot
  for entity in BENCHMARK_ENTITIES:
      entity_data = performance_df[performance_df['Entity'] == entity]
      fig.add_trace(go.Bar(x=entity_data['Year'], y=entity_data['Annual Return'], name=f'{entity} Annual Return', marker_color=color_map[entity], text=entity_data['Entity'], textposition='auto'), row=1, col=1)

  # Sharpe Ratio plot
  for entity in BENCHMARK_ENTITIES:
      entity_data = performance_df[performance_df['Entity'] == entity]
      fig.add_trace(go.Bar(x=entity_data['Year'], y=entity_data['Sharpe Ratio'], name=f'{entity} Sharpe Ratio', marker_color=color_map[entity], text=entity_data['Entity'], textposition='auto'), row=2, col=1)

  # Beta (vs SPY) plot
  for entity in BENCHMARK_ENTITIES:
      entity_data = performance_df[performance_df['Entity'] == entity]
      fig.add_trace(go.Bar(x=entity_data['Year'], y=entity_data['Beta (vs SPY)'], name=f'{entity} Beta (vs SPY)', marker_color=color_map[entity], text=entity_data['Entity'], textposition='auto'), row=3, col=1)

  # Alpha (vs SPY) plot
  for entity in BENCHMARK_ENTITIES:
      entity_data = performance_df[performance_df['Entity'] == entity]
      fig.add_trace(go.Bar(x=entity_data['Year'], y=entity_data['Alpha (vs SPY)'], name=f'{entity} Alpha (vs SPY)', marker_color=color_map[entity], text=entity_data['Entity'], textposition='auto'), row=4, col=1)

  # Max Drawdown plot
  for entity in BENCHMARK_ENTITIES:
      entity_data = performance_df[performance_df['Entity'] == entity]
      fig.add_trace(go.Bar(x=entity_data['Year'], y=entity_data['Max Drawdown'], name=f'{entity} Max Drawdown', marker_color=color_map[entity], text=entity_data['Entity'], textposition='auto'), row=5, col=1)

  # Update layout
  fig.update_layout(height=1500, title_text="Portfolio Performance Metrics Over Time", barmode='group')
  return fig

@dash_app.callback(Output("technical-charts", "figure"), [Input("update-button", "n_clicks")], [State("ticker-input", "value")])
def update_technicals_charts(n, ticker):  
    print("Fetching technical data...")
    techical_bundle = TechnicalAnalyzer.main(us, ps, ticker, 'normal')
    
    fig = techical_bundle['figs']

    fig.update_layout(
        autosize = True,
        title="Technical Charts",
        template="darkly"
    )   
    
    return fig

@dash_app.callback(Output("market-icicle-selection", "children"),
                   Input("market-watchlist-icicle", "clickData"))
def show_icicle_click(clickData):
    if not clickData or not clickData.get("points"):
        raise PreventUpdate
    pt = clickData["points"][0]
    label = pt.get("label") or ""
    node_id = pt.get("id") or label or ""
    node_id_lower = str(node_id).lower()

    # If not a "themes" or "status update" bucket, treat as ticker and fetch Perplexity
    if node_id and ("themes" not in node_id_lower and "status update" not in node_id_lower):
        symbol = str(node_id).split("/")[-1] or label
        symbol = (symbol or "").upper()
        try:
            mrk = MarketExplorer.MarketExplorer(us, ps)
            res = mrk.get_perplexity_summaries([{"symbol": symbol}], days=1)  # returns {symbol: summary}
            summary = res.get(symbol, "No summary available.")
        except Exception as e:
            summary = f"Error fetching summary for {symbol}: {e}"

        return html.Div([
            html.H5(f"{symbol} — Summary"),
            dcc.Markdown(summary)
        ])

    # Otherwise (theme/status buckets), just echo the selection
    if not label:
        label = node_id.split("/")[-1] if node_id else "N/A"
    return f"Selected: {label}"

@dash_app.callback(Output("market-watchlist-icicle", "figure"), [Input("update-button", "n_clicks")])
def market_watchlist_icicle_chart(n):
    mrk = MarketExplorer.MarketExplorer(us, ps)
    fig = mrk.plot_combined_watchlists_icicle()
    return fig

@dash_app.callback(Output("market-graph", "figure"), [Input("update-button", "n_clicks")])
def update_market_charts(n):  
    
    print("Fetching Market data...")
    market_data = MarketExplorer.main(us, ps, 'local', True)
    
    # Assuming market_data contains multiple figures
    figures = [
        market_data['top_movers_sp500_up']['fig'],
        market_data['top_movers_sp500_down']['fig'],
        #market_data['top_100']['fig']
    ]
    
    combined_fig = combine_figures_into_subplots(figures, list(market_data.keys()))
    
    return combined_fig

    
def create_news_cards():
    # Fetch market data
    market_data = MarketExplorer.main(us, ps, 'local', True)
    # Merge news data from sources
    all_news = {}
    if 'top_movers_sp500_up' in market_data and 'news' in market_data['top_movers_sp500_up']:
        all_news.update(market_data['top_movers_sp500_up']['news'])
    if 'top_movers_sp500_down' in market_data and 'news' in market_data['top_movers_sp500_down']:
        all_news.update(market_data['top_movers_sp500_down']['news'])

    # Use Perplexity summaries precomputed by MarketExplorer
    pplx_summaries = {}
    if 'top_movers_sp500_up' in market_data and 'perplexity_summaries' in market_data['top_movers_sp500_up']:
        pplx_summaries.update(market_data['top_movers_sp500_up']['perplexity_summaries'])
    if 'top_movers_sp500_down' in market_data and 'perplexity_summaries' in market_data['top_movers_sp500_down']:
        pplx_summaries.update(market_data['top_movers_sp500_down']['perplexity_summaries'])

    from collections import Counter
    from statistics import mean

    cards = []
    for ticker, payload in all_news.items():
        targets = (payload or {}).get('data', {}).get('target', []) or []
        # Keep only items that match the ticker symbol
        items = [it for it in targets if str(it.get('symbol', '')).lower() == ticker.lower()]
        if not items:
            continue

        # Aggregate metrics
        def _to_float(x):
            try:
                return float(x)
            except Exception:
                return None

        sentiments = [_to_float(it.get('sentimentScore')) for it in items]
        sentiments = [s for s in sentiments if s is not None]
        avg_sent = f"{mean(sentiments):.2f}" if sentiments else "N/A"

        ratings = [str(it.get('sentimentRating')) for it in items if it.get('sentimentRating')]
        top_rating = Counter(ratings).most_common(1)[0][0] if ratings else "N/A"

        impacts = [str(it.get('stockImpactRating')) for it in items if it.get('stockImpactRating')]
        top_impact = Counter(impacts).most_common(1)[0][0] if impacts else "N/A"

        # Headlines (unique, up to 3)
        seen = set()
        headlines = []
        for it in items:
            title = (it.get('title') or "").strip()
            if title and title not in seen:
                headlines.append(title)
                seen.add(title)
            if len(headlines) == 3:
                break

        # Links (first available)
        first_link = next((it.get('url') for it in items if it.get('url')), "#")

        # Build content and bullets
        content = f"Stories: {len(items)} | Avg sentiment: {avg_sent} | Top sentiment: {top_rating} | Impact: {top_impact}"
        bullets = "\n".join(f"- {h}" for h in headlines) if headlines else "- No headlines available"

        # Perplexity summary fetched in parallel
        pplx_md = pplx_summaries.get(ticker, "Perplexity summary unavailable.")

        # One card per ticker
        card = dbc.Card(
            [
                dbc.CardBody([
                    dbc.CardHeader(f"{ticker} — News Summary"),
                    dcc.Markdown(bullets),
                    html.P(content, className="text-muted mb-1"),
                    html.Hr(),
                    html.H6("Perplexity (last 1 day)"),
                    dcc.Markdown(pplx_md),
                    dbc.Button("Primary Link", href=first_link, color="primary", className="mt-2"),
                ])
            ],
            className="mb-3"
        )
        cards.append(card)

    return cards


@dash_app.callback(
    [Output("fundamental-timechart", "figure"), Output("fundamental-sankey", "figure")],
    [Input("update-button", "n_clicks")],
    [State("ticker-input", "value")])
def update_fundamental_charts(n, ticker):
    if not ticker:
        return go.Figure(), go.Figure()

    def _error_fig(msg: str):
        fig = go.Figure()
        fig.add_annotation(text=f"Fundamentals error: {msg}", showarrow=False, font=dict(color="red"))
        fig.update_layout(template="plotly_dark", title="Fundamentals")
        return fig

    try:
        # Fetch quarterly fundamentals using the new analyzer
        tidy, pivots, errs = FundamentalAnalyzer.run_quarterly_comparison(
            [ticker], since="2018Q1", api_key=None, data_dir="../data/stock_fundamental/", plot=False
        )
    except Exception as e:
        return _error_fig(str(e)), go.Figure()

    if errs and errs.get(ticker):
        return _error_fig(errs[ticker]), go.Figure()

    cashflow_df = tidy.get("cashflow", pd.DataFrame())
    income_df = tidy.get("income", pd.DataFrame())

    # 1) Time-series chart
    if not cashflow_df.empty:
        df_plot = cashflow_df[
            (cashflow_df["Ticker"] == ticker)
            & (cashflow_df["Metric"].isin(["CFO", "CapEx", "Free Cash Flow"]))
        ].copy().sort_values("Report Date")
        title = f"{ticker} Cash Flow (Quarterly)"
    else:
        df_plot = income_df[
            (income_df["Ticker"] == ticker)
            & (income_df["Metric"].isin(["Revenue", "Operating Income", "Net Income"]))
        ].copy().sort_values("Report Date")
        title = f"{ticker} Income Statement (Quarterly)"

    if df_plot.empty:
        time_fig = _error_fig("No quarterly fundamentals available.")
    else:
        time_fig = px.line(
            df_plot, x="Report Date", y="Value", color="Metric", markers=True, title=title
        )
        time_fig.update_layout(template="darkly", legend_orientation="h")

    # 2) Sankey for the most recent quarter (cash flow)
    sankey_fig = go.Figure()
    sankey_fig.update_layout(template="plotly_dark", title=f"{ticker} Cash Flow Breakdown (Most Recent Quarter)")

    if not cashflow_df.empty:
        sub = cashflow_df[cashflow_df["Ticker"] == ticker].copy()

        # Select the most recent YearQ
        sub = sub.dropna(subset=["Year", "QuarterNum"])
        if not sub.empty:
            sub = sub.sort_values(["Year", "QuarterNum", "Report Date"])
            last_yq = sub.iloc[-1]["YearQ"]
            recent = sub[sub["YearQ"] == last_yq].pivot_table(index="YearQ", columns="Metric", values="Value", aggfunc="last")

            cfo = float(recent.iloc[0].get("CFO", 0.0)) if not recent.empty else 0.0
            capex = float(recent.iloc[0].get("CapEx", 0.0)) if not recent.empty else 0.0
            fcf = float(recent.iloc[0].get("Free Cash Flow", 0.0)) if not recent.empty else 0.0

            # Build a simple CFO -> CapEx, CFO -> FCF Sankey with positive magnitudes
            nodes = ["CFO", "CapEx", "Free Cash Flow"]
            idx = {name: i for i, name in enumerate(nodes)}
            values = [
                abs(capex) if abs(capex) > 0 else 0.0,
                abs(fcf) if abs(fcf) > 0 else 0.0
            ]

            sankey_fig = go.Figure(data=[go.Sankey(
                node=dict(
                    pad=20,
                    thickness=18,
                    line=dict(color="white", width=0.5),
                    label=nodes,
                    color=["#2a9d8f", "#e76f51", "#264653"]
                ),
                link=dict(
                    source=[idx["CFO"], idx["CFO"]],
                    target=[idx["CapEx"], idx["Free Cash Flow"]],
                    value=values,
                    color=["#e76f51", "#264653"]
                )
            )])
            sankey_fig.update_layout(
                template="plotly_dark",
                title=f"{ticker} Cash Flow Breakdown — {last_yq}"
            )

    return time_fig, sankey_fig

#@dash_app.callback(
#    [Output("fundamental-timechart", "figure"), Output("fundamental-sankey", "figure")],
#    [Input("update-button", "n_clicks")],
#    [State("ticker-input", "value")])
def update_fundamental_charts_old(n, ticker):  
  f = FundamentalAnalyzer

  fundamental_bundle = f.main(ticker, 'local')

  print(len(fundamental_bundle['figs']))

  return fundamental_bundle['figs'][0], fundamental_bundle['figs'][1]



@dash_app.callback(Output("sample-graph", "figure"), [Input("update-button", "n_clicks")])
def update_chart(n):
    x_vals = [1, 2, 3]
    y_vals = [i * (n or 1) for i in [4, 1, 2]]
    
    fig = go.Figure(data=[
        go.Bar(x=x_vals, y=y_vals, name="Sample")
    ])
    
    fig.update_layout(
        autosize = True,
        title="Demo Chart",
        template="darkly"
    )   
    return fig

# Protect the Dash app with Flask-Login
@dash_app.server.before_request
def protect_dash():
    # If the user is not authenticated and tries to access /dash/, redirect to /login
    if not current_user.is_authenticated and request.path.startswith("/dash/"):
        return redirect(url_for("login"))

# ----------------------------------------------------------------
# Flask routes
# ----------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")
        if password == app.secret_key:  # silly example check
            user = User("testuser")
            login_user(user)
            return redirect(url_for("home"))
        return "Invalid password."
    # Render the login page with our pure HTML navbar
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
      <link href="https://fonts.googleapis.com/css2?family=Mokoto&display=swap" rel="stylesheet">
      <style>
        body {
          font-family: 'Mokoto', sans-serif;
        }
      </style>
    </head>
    <body class="bg-dark text-white">
      {{ navbar|safe }}  <!-- Render the navbar (HTML string) -->
      <div class="container mt-5">
        <div class="row justify-content-center">
          <div class="col-md-4">
            <h3 class="text-center">Login</h3>
            <form method="post">
              <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" class="form-control bg-secondary text-white" required>
              </div>
              <button type="submit" class="btn btn-primary btn-block mt-3">Login</button>
            </form>
          </div>
        </div>
      </div>

      <!-- JS for Bootstrap toggler, etc. -->
      <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
      <script src="https://cdn.jsdelivr.net/npm/bootstrap@4.5.2/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    ''', navbar=get_flask_navbar(active="Home"))

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def home():
    # Render a home page with fade-in effect
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" 
            href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
      <style>
        .fade-in {
          animation: fadein 2s;
        }
        @keyframes fadein {
          from {opacity: 0;}
          to {opacity: 1;}
        }
        body {
          background: url('assets/logo/background/Colorlogowithbackground.svg') no-repeat center center fixed;
          background-size: cover;
          font-family: 'Mokoto', sans-serif;
        }
      </style>
    </head>
    <body class="bg-dark text-white fade-in">
      {{ navbar|safe }}  <!-- Render the navbar (HTML string) -->
      <div class="container mt-5">
        <h1 class="text-center mb-4"></h1>
        <p class="text-center"></p>
      </div>

      <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
      <script src="https://cdn.jsdelivr.net/npm/bootstrap@4.5.2/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    ''', navbar=get_flask_navbar(active="Home"))

@app.route("/routes")
def list_routes():
    import urllib
    output = []
    for rule in app.url_map.iter_rules():
        options = {}
        for arg in rule.arguments:
            options[arg] = "[{0}]".format(arg)
        methods = ','.join(rule.methods)
        url = url_for(rule.endpoint, **options)
        line = urllib.parse.unquote("{:50s} {:20s} {}".format(rule.endpoint, methods, url))
        output.append(line)
    return "<br>".join(sorted(output))

# ----------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
