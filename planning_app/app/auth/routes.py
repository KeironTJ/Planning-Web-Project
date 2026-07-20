"""
Auth blueprint routes.

Routes are thin: they handle HTTP concerns (redirects, flash messages,
session management) and delegate business logic to AuthService.
"""

from flask import render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user

from . import auth_bp
from .forms import (
    LoginForm, RegistrationForm, PasswordResetRequestForm,
    PasswordResetForm, ChangePasswordForm, EditProfileForm,
)
from .services import AuthService
from app.extensions import login_manager
from app.core.exceptions import AuthorisationError, DuplicateError, ValidationError, NotFoundError
from .models import User


@login_manager.user_loader
def load_user(user_id: str):
    """Flask-Login callback: load a User by primary key from the session."""
    return User.query.get(int(user_id))


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = AuthService.login(
                login=form.login.data,
                password=form.password.data,
                ip=request.remote_addr,
            )
            login_user(user, remember=form.remember.data)
            next_page = request.args.get("next")
            # Security: only allow relative redirects to prevent open redirect
            if next_page and next_page.startswith("/"):
                return redirect(next_page)
            return redirect(url_for("index"))
        except AuthorisationError as e:
            flash(str(e), "danger")

    return render_template("auth/login.html", form=form, title="Sign In")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("capacity.dashboard"))

    form = RegistrationForm()
    if form.validate_on_submit():
        try:
            AuthService.register_user(
                username=form.username.data,
                email=form.email.data,
                password=form.password.data,
                first_name=form.first_name.data or "",
                last_name=form.last_name.data or "",
                department=form.department.data or "",
            )
            flash("Registration successful. Please sign in.", "success")
            return redirect(url_for("auth.login"))
        except (DuplicateError, ValidationError) as e:
            flash(str(e), "danger")

    return render_template("auth/register.html", form=form, title="Register")


# ---------------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------------

@auth_bp.route("/password-reset/request", methods=["GET", "POST"])
def password_reset_request():
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        token = AuthService.generate_password_reset_token(form.email.data)
        if token:
            # In production, send via email; for now log it
            reset_url = url_for("auth.password_reset", token=token, _external=True)
            current_app.logger.info(f"Password reset URL: {reset_url}")
            # TODO: send_reset_email(form.email.data, reset_url)
        # Always show success to avoid leaking account existence
        flash("If that email is registered, a reset link has been sent.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/password_reset_request.html", form=form, title="Reset Password")


@auth_bp.route("/password-reset/<token>", methods=["GET", "POST"])
def password_reset(token: str):
    form = PasswordResetForm()
    if form.validate_on_submit():
        try:
            AuthService.reset_password(token, form.password.data)
            flash("Password reset successfully. Please sign in.", "success")
            return redirect(url_for("auth.login"))
        except (ValidationError, NotFoundError) as e:
            flash(str(e), "danger")
            return redirect(url_for("auth.password_reset_request"))
    return render_template("auth/password_reset.html", form=form, title="Set New Password")


# ---------------------------------------------------------------------------
# Profile & Password Change
# ---------------------------------------------------------------------------

@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = EditProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.first_name = form.first_name.data
        current_user.last_name = form.last_name.data
        current_user.department = form.department.data
        from app.extensions import db
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", form=form, title="My Profile")


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            AuthService.change_password(
                current_user,
                form.current_password.data,
                form.new_password.data,
            )
            flash("Password changed successfully.", "success")
            return redirect(url_for("auth.profile"))
        except (AuthorisationError, ValidationError) as e:
            flash(str(e), "danger")
    return render_template("auth/change_password.html", form=form, title="Change Password")


# ---------------------------------------------------------------------------
# Site switcher
# ---------------------------------------------------------------------------

@auth_bp.route("/switch-site/<int:site_id>", methods=["POST"])
@login_required
def switch_site(site_id: int):
    """
    Set the active site for the current session.

    Admin users may switch to any active site.
    All other users may only switch to sites they have been granted access to.
    """
    from app.admin.models import Site

    site = Site.query.filter_by(id=site_id, is_active=True).first_or_404()

    if not current_user.is_admin and site not in current_user.sites:
        flash("You do not have access to that site.", "danger")
        return redirect(request.referrer or url_for("capacity.dashboard"))

    session["active_site_id"] = site.id
    session["active_site_name"] = site.name
    flash(f"Switched to {site.name}.", "success")
    next_page = request.form.get("next") or request.referrer
    if next_page and next_page.startswith("/"):
        return redirect(next_page)
    return redirect(url_for("capacity.dashboard"))
