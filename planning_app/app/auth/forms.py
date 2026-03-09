"""
WTForms form definitions for the auth blueprint.

All forms include CSRF protection via Flask-WTF.
Validation errors are surfaced through the standard WTForms API so
templates can render them consistently.
"""

from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, BooleanField, SelectMultipleField,
    SubmitField, TextAreaField,
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, Length, Optional, ValidationError
)
from .models import User


class LoginForm(FlaskForm):
    """Login with email + password."""

    email = StringField(
        "Email",
        validators=[DataRequired(), Email()],
        render_kw={"placeholder": "you@company.com", "autocomplete": "email"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired()],
        render_kw={"autocomplete": "current-password"},
    )
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign In")


class RegistrationForm(FlaskForm):
    """New user self-registration (if enabled by admin)."""

    username = StringField(
        "Username",
        validators=[DataRequired(), Length(min=3, max=64)],
        render_kw={"placeholder": "john.smith"},
    )
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    first_name = StringField("First Name", validators=[Optional(), Length(max=50)])
    last_name = StringField("Last Name", validators=[Optional(), Length(max=50)])
    department = StringField("Department", validators=[Optional(), Length(max=100)])
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=10)],
    )
    password_confirm = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Register")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("That username is already taken.")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data.lower()).first():
            raise ValidationError("An account with that email already exists.")


class PasswordResetRequestForm(FlaskForm):
    """Request a password reset email."""

    email = StringField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Send Reset Link")


class PasswordResetForm(FlaskForm):
    """Set a new password using a valid reset token."""

    password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=10)],
    )
    password_confirm = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset Password")


class ChangePasswordForm(FlaskForm):
    """Authenticated user changing their own password."""

    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=10)],
    )
    new_password_confirm = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Change Password")


class EditProfileForm(FlaskForm):
    """Edit own profile details."""

    first_name = StringField("First Name", validators=[Optional(), Length(max=50)])
    last_name = StringField("Last Name", validators=[Optional(), Length(max=50)])
    department = StringField("Department", validators=[Optional(), Length(max=100)])
    submit = SubmitField("Save Changes")
