"""Role checks and template helpers."""

from __future__ import annotations

from datetime import date, datetime
from functools import wraps

from flask import abort, current_app
from flask_login import current_user, login_required
from markupsafe import Markup, escape

from tims_core.calc import round_with_uncertainty

from .models import now


def role_required(*roles: str):
    """Allow the view only for users whose role passes ``User.can(*roles)``."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.can(*roles):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def fmt_pair(value, U) -> tuple[str, str]:
    if value is None:
        return "", ""
    return round_with_uncertainty(float(value), float(U or 0))


def iso_html(mass, symbol: str) -> Markup:
    return Markup(f"<sup>{int(mass)}</sup>{escape(symbol)}")


def ratio_html(label: str, symbol: str) -> Markup:
    num, den = label.split("/")
    return Markup(f"{iso_html(num, symbol)}/{iso_html(den, symbol)}")


def fmt_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d) if "T" in d else date.fromisoformat(d)
        except ValueError:
            return d
    if isinstance(d, datetime):
        return d.strftime("%d-%m-%Y %H:%M")
    return d.strftime("%d-%m-%Y")


def register_template_helpers(app) -> None:
    app.jinja_env.globals.update(
        fmt_pair=fmt_pair, iso_html=iso_html, ratio_html=ratio_html,
        software_version=lambda: current_app.config["SOFTWARE_VERSION"],
        now=now,
    )
    app.jinja_env.filters["fdate"] = fmt_date
    app.jinja_env.filters["sig"] = lambda v, n=6: "" if v is None else f"{float(v):.{n}g}"
