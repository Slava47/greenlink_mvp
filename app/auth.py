from functools import wraps
from flask import session, redirect, url_for, flash, request, g
from werkzeug.security import generate_password_hash, check_password_hash
from .db import get_db

def hash_password(pw: str) -> str:
    return generate_password_hash(pw)

def verify_password(hash_: str, pw: str) -> bool:
    return check_password_hash(hash_, pw)

def current_user():
    if "user_id" not in session:
        return None
    if getattr(g, "_current_user", None) is None:
        db = get_db()
        g._current_user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return g._current_user

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("main.login", next=request.path))
        if u["is_blocked"]:
            session.clear()
            flash("Ваш аккаунт заблокирован. Обратитесь к администратору.", "error")
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)
    return wrapped

def roles_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            u = current_user()
            if not u:
                return redirect(url_for("main.login", next=request.path))
            if u["role"] not in roles:
                flash("Недостаточно прав для доступа к разделу.", "error")
                return redirect(url_for("main.index"))
            return view(*args, **kwargs)
        return wrapped
    return deco
