"""Login, logout, password change, account lockout (FR-DATA-05)."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select

from . import db
from .models import User, now
from .services import audit

bp = Blueprint("auth", __name__)


def password_problem(password: str, username: str = "") -> str | None:
    n = current_app.config["PASSWORD_MIN_LENGTH"]
    if len(password) < n:
        return f"The password must have at least {n} characters."
    if username and password.lower() == username.lower():
        return "The password must not be the same as the username."
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.session.scalars(select(User).where(User.username == username)).first()
        if user is None or not user.active:
            audit("login_failed", "user", username, username=username or "-")
            db.session.commit()
            flash("Wrong username or password.", "error")
            return render_template("login.html"), 401
        if user.locked_until and user.locked_until > now():
            flash(f"This account is locked until {user.locked_until:%H:%M} after too many failed logins.", "error")
            return render_template("login.html"), 401
        if not user.check_password(password):
            user.failed_logins += 1
            audit("login_failed", "user", user.username, username=user.username)
            if user.failed_logins >= current_app.config["LOCKOUT_ATTEMPTS"]:
                user.locked_until = now() + timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])
                user.failed_logins = 0
                audit("account_locked", "user", user.username, username=user.username)
                flash(f"Too many failed logins. The account is locked for "
                      f"{current_app.config['LOCKOUT_MINUTES']} minutes.", "error")
            else:
                flash("Wrong username or password.", "error")
            db.session.commit()
            return render_template("login.html"), 401
        user.failed_logins = 0
        user.locked_until = None
        login_user(user)
        audit("login", "user", user.username)
        db.session.commit()
        nxt = request.args.get("next", "")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = url_for("main.index")
        return redirect(nxt)
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("logout", "user", current_user.username)
    db.session.commit()
    logout_user()
    flash("You have logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not current_user.check_password(current):
            flash("The current password is wrong.", "error")
        elif new != request.form.get("confirm", ""):
            flash("The two new passwords do not match.", "error")
        elif current_user.check_password(new):
            flash("The new password must be different from the current one.", "error")
        elif (problem := password_problem(new, current_user.username)):
            flash(problem, "error")
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            audit("password_changed", "user", current_user.username)
            db.session.commit()
            flash("Password changed.", "info")
            return redirect(url_for("main.index"))
    return render_template("change_password.html")
