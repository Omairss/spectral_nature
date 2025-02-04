from flask import Flask, redirect, url_for, request, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc

app = Flask(__name__)
app.secret_key = "a"

login_manager = LoginManager()
login_manager.init_app(app)

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")
        if password == app.secret_key:
            user = User("testuser")
            login_user(user)
            return redirect(url_for("home"))
        return "Invalid password."
    return '''
    <!DOCTYPE html>
    <html>
    <head>
      <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    </head>
    <body class="bg-light">
      <div class="container mt-5">
        <div class="row justify-content-center">
          <div class="col-md-4">
            <h3 class="text-center">Login</h3>
            <form method="post">
              <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" class="form-control" required>
              </div>
              <button type="submit" class="btn btn-primary btn-block">Login</button>
            </form>
          </div>
        </div>
      </div>
    </body>
    </html>
    '''

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def home():
    return redirect("/dash/")

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

# Initialize Dash app
dash_app = dash.Dash(
    __name__,
    server=app,
    routes_pathname_prefix="/dash/",
    external_stylesheets=[dbc.themes.BOOTSTRAP]
)

dash_app.layout = dbc.Container([
    dcc.Location(id="url"),
    html.Div([
        html.H1("Protected Dash Section"),
        dcc.Tabs(id="tabs", value='tab1', children=[
            dcc.Tab(label='Demo Chart', value='tab1'),
            dcc.Tab(label='Another Page', value='tab2'),
        ]),
        html.Div(id='tabs-content')
    ])
], fluid=True)

@dash_app.callback(Output('tabs-content', 'children'), [Input('tabs', 'value')])
def render_content(tab):
    if tab == 'tab1':
        return html.Div([
            dbc.Button("Update Chart", id="update-button", color="primary"),
            dcc.Graph(id="sample-graph")
        ])
    else:
        return html.Div("Another Page Content")

@dash_app.callback(Output("sample-graph", "figure"), [Input("update-button", "n_clicks")])
def update_chart(n):
    x_vals = [1, 2, 3]
    y_vals = [i * (n or 1) for i in [4, 1, 2]]
    return {
        "data": [{"x": x_vals, "y": y_vals, "type": "bar", "name": "Sample"}],
        "layout": {"title": "Demo Chart"}
    }

@dash_app.server.before_request
def protect_dash():
    if not current_user.is_authenticated and request.path.startswith("/dash/"):
        return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)