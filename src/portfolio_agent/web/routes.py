"""The complete route registry (single source for the server, tests and the web eval)."""
from .app import session_routes
from .routes_operator import operator_routes
from .routes_portfolio import portfolio_routes


def default_routes():
    return session_routes() + portfolio_routes() + operator_routes()
