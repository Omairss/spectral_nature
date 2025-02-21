import dash
import pandas as pd
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
from flask import Flask, redirect, url_for, request, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import plotly.graph_objects as go  
import plotly.express as px
from plotly.subplots import make_subplots

import sys
sys.path.append("..")
import MarketExplorer, TechnicalAnalyzer, OptionFinder, CurrentStatus, LinkedAuth

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

us, ps = LinkedAuth.get_creds('test')

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
        ], className="mt-3")
    # Market Opportunity
    if tab == 'tab2':
        return html.Div([
          dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
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
                dcc.Graph(id="sample-graph")
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
    
  print("Fetching portfolio data...")

  current_status = CurrentStatus.main(us, ps, 'all', 'local')

  # Convert the 'equity_historicals' list of dicts to a pandas DataFrame
  historical_df = pd.DataFrame(current_status['historical_df']['equity_historicals'])

  # Convert 'begins_at' to datetime
  historical_df['begins_at'] = pd.to_datetime(historical_df['begins_at'])

  # Convert 'close_equity' field to numeric
  historical_df['adjusted_close_equity'] = pd.to_numeric(historical_df['adjusted_close_equity'], errors='coerce')

  # Sort by date
  historical_df.sort_values(by='begins_at', inplace=True)

  # Plot the data using Plotly
  fig = px.line(historical_df, x='begins_at', y='adjusted_close_equity', title='Robinhood Portfolio Value Over Time', labels={'begins_at': 'Date', 'adjusted_close_equity': 'Equity ($)'})
  fig.update_layout(template='plotly_dark')
  return fig

@dash_app.callback(Output("earnings-barchart", "figure"), [Input("update-button", "n_clicks")])
def normalized_earnings_chart(n):
    
  current_status = CurrentStatus.main(us, ps, 'all', 'local')
  holdings_df = pd.DataFrame(current_status['holdings_df']).T
  holdings_df['pe_ratio_abs'] = holdings_df['pe_ratio'].apply(lambda x: max(float(x), 1))
  holdings_df['equity_normalized_pe_ratio'] = holdings_df['equity'].astype(float) / holdings_df['pe_ratio'].astype(float)
  holdings_df[['name', 'equity', 'equity_normalized_pe_ratio']].sort_values(by = 'equity_normalized_pe_ratio', ascending = False)
  # Select the columns to plot
  columns_to_plot = ['equity', 'equity_normalized_pe_ratio']

  # Melt the dataframe to long format
  holdings_melted = holdings_df[['name'] + columns_to_plot].melt(id_vars='name', value_vars=columns_to_plot, var_name='Metric', value_name='Value')
  holdings_melted['Value'] = holdings_melted['Value'].astype(float)
  
  # Create the grouped bar chart
  fig = px.bar(holdings_melted, x='name', y='Value', color='Metric', barmode='group', title='Holdings Equity and Equity Normalized PE Ratio', log_y=True)
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

@dash_app.callback(Output("market-graph-old", "figure"), [Input("update-button", "n_clicks")])
def update_market_charts_old(n):  
    
    print("Fetching Market data...")
    market_bundle = MarketExplorer.main(us, ps, 'local',  True)
    
    fig = market_bundle['top_movers_sp500_up']['fig']

    fig.update_layout(
        autosize = True,
        title="Technical Charts",
        template="darkly"
    )   
    
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


def create_news_cards_():
    
    print('Creating news cards...')

    news_data = [
    {"title": "Title 1", "content": "News content 1", "image": "https://via.placeholder.com/150", "link": "#"},
    {"title": "Title 2", "content": "News content 2", "image": "https://via.placeholder.com/150", "link": "#"},
    {"title": "Title 3", "content": "News content 3", "image": "https://via.placeholder.com/150", "link": "#"},
    ]
    cards = []
    for news in news_data:
        card = dbc.Card(
            [
                dbc.CardImg(src=news["image"], top=True),
                dbc.CardBody([
                    dbc.CardHeader(news["title"]),
                    html.P(news["content"]),
                    dbc.Button("Read more", href=news["link"], color="primary")
                ])
            ],
            className="mb-3"
        )
        cards.append(card)
    return cards

    
# Function to create news cards with images and links
def create_news_cards():

    market_data = MarketExplorer.main(us, ps, 'local', True)
    
    print('Creating news cards...')

    # Initialize an empty list to store all news items
    all_news_list = []

    # Merge news data from different sources
    all_news = {}
    all_news.update(market_data['top_movers_sp500_up']['news'])
    all_news.update(market_data['top_movers_sp500_down']['news'])

    print(all_news)

    selected_fields = ['symbol', 'title', 'summary', 'sentimentRating', 'priceImpactReasoning', 'stockImpactRating', 'impactHorizonReasoning', 'sentimentScore']

    for ticker, data in all_news.items():
        for item in data['data']['target']:
            news = {}
            news['title'] = f"{item['symbol']} : {item['title']}"
            print(news['title'])
            news['content'] = " | ".join([f"{field}: {item.get(field, 'N/A')}" for field in selected_fields])
            news['link'] = item['url']
            all_news_list.append(news)

    print('All news')
    print(all_news_list)

    cards = []
    for news in all_news_list:
        card = dbc.Card(
            [
                # dbc.CardImg(src=news["image"], top=True),
                dbc.CardBody([
                    dbc.CardHeader(news["title"]),
                    html.P(news["content"]),
                    dbc.Button("Read more", href=news["link"], color="primary")
                ])
            ],
            className="mb-3"
        )
        cards.append(card)
    return cards

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
