import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc
from flask import Flask, redirect, url_for, request, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import plotly.graph_objects as go  


# ----------------------------------------------------------------
# Set Plotly theme to dark
# ----------------------------------------------------------------
from dash_bootstrap_templates import load_figure_template

# loads the "darkly" template and sets it as the default
load_figure_template("darkly")

# ----------------------------------------------------------------
# Flask app setup
# ----------------------------------------------------------------

app = Flask(__name__)
app.secret_key = "das"  # for demonstration

login_manager = LoginManager()
login_manager.init_app(app)

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
                html.Img(src="assets/logo/rectangle/Color logo - no background.svg", height="80px"),
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
            dcc.Tab(label='Benchmarker', value='tab1', className="bg-dark text-white"),
            dcc.Tab(label='Performance', value='tab2', className="bg-dark text-white"),
            dcc.Tab(label='Option Booster', value='tab3', className="bg-dark text-white"),

        ]),
        html.Div(id='tabs-content', className="text-white mt-3")
    ])
], fluid=True)

# Callbacks for the Dash app
@dash_app.callback(Output('tabs-content', 'children'), [Input('tabs', 'value')])
def render_content(tab):
    if tab == 'tab1':
        return html.Div([
            dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
            dcc.Graph(id="sample-graph")
        ], className="mt-3")
    if tab == 'tab2':
        return html.Div([
                dbc.Button("Update Chart", id="update-button", color="primary", className="mb-3"),
                dcc.Graph(id="sample-graph")
                ], className="mt-3")
    else:
        return html.Div("Coming soon", className="mt-3")

@dash_app.callback(Output("sample-graph", "figure"), [Input("update-button", "n_clicks")])
def update_chart(n):
    x_vals = [1, 2, 3]
    y_vals = [i * (n or 1) for i in [4, 1, 2]]
    
    fig = go.Figure(data=[
        go.Bar(x=x_vals, y=y_vals, name="Sample")
    ])
    
    fig.update_layout(
        title="Demo Chart",
        template="darkly"  # Enables dark mode for the chart
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
      </style>
    </head>
    <body class="bg-dark text-white fade-in">
      {{ navbar|safe }}  <!-- Render the navbar (HTML string) -->
      <div class="container mt-5">
        <h1 class="text-center mb-4">Welcome to the Dark Themed App</h1>
        <p class="text-center">Enjoy a unified dark experience across all pages.</p>
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
    app.run(host="0.0.0.0", port=5000, debug=True)
