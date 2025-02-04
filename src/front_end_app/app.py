from flask import Flask, redirect, url_for, request, render_template_string
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import dash
from dash import dcc, html

app = Flask(__name__)
app.secret_key = "super_secret_key"

login_manager = LoginManager()
login_manager.init_app(app)

# Dummy user
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
            return redirect(url_for("index"))
        else:
            return "Invalid password."
    return render_template_string('''
        <form method="post">
            Password: <input type="password" name="password">
            <input type="submit" value="Login">
        </form>
    ''')

@app.route("/logout")
def logout():
    logout_user()
    return "Logged out."

@app.route("/")
def index():
    return "Go to /login to authenticate, then /dash/ to view the Dash app."

# Create Dash app, embed it on Flask's server
dash_app = dash.Dash(__name__, server=app, url_base_pathname="/dash/")

dash_app.layout = html.Div([
    html.H1("Protected Dash Section"),
    dcc.Graph(
        figure=dict(
            data=[
                dict(x=[1, 2, 3], y=[4, 1, 2], type='bar', name='Sample')
            ],
            layout=dict(title='Demo Chart')
        )
    )
])

@dash_app.server.before_request
def protect_dash():
    if not current_user.is_authenticated and request.path.startswith("/dash/"):
        return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)