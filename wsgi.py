"""Entry point for Gunicorn/Waitress and the `flask` command: `flask --app wsgi ...`."""

from app import create_app

app = create_app()
