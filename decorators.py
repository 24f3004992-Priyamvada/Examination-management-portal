from functools import wraps
from flask import abort, redirect, url_for
from flask_login import current_user


def role_required(role):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role != role:
                abort(403)
            if role == "examiner" and current_user.examiner_profile.status != "Approved":
                return redirect(url_for("auth.pending"))
            return view(*args, **kwargs)
        return wrapped
    return decorator