"""Authentication and account forms for ChainWatch Pro."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, HiddenField, PasswordField, SelectField, StringField, SubmitField
from wtforms.fields import EmailField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, ValidationError

from app.utils.validators import validate_password_strength


class RegistrationForm(FlaskForm):
    """Sign-up form for new organisation admins and team members."""

    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=100)])
    last_name = StringField("Last Name", validators=[DataRequired(), Length(min=2, max=100)])
    company_name = StringField("Company Name", validators=[DataRequired(), Length(min=2, max=255)])
    email = EmailField("Work Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    role_title = StringField("Role Title", validators=[Optional(), Length(max=100)])
    company_size = SelectField(
        "Company Size",
        validators=[DataRequired()],
        choices=[
            ("1-10", "1-10"),
            ("11-50", "11-50"),
            ("51-200", "51-200"),
            ("201-500", "201-500"),
            ("501-1000", "501-1000"),
            ("1000+", "1000+"),
        ],
    )
    monthly_shipment_volume = SelectField(
        "Monthly Shipment Volume",
        validators=[DataRequired()],
        choices=[
            ("Under 50", "Under 50"),
            ("50-200", "50-200"),
            ("200-500", "200-500"),
            ("500-2000", "500-2000"),
            ("2000+", "2000+"),
        ],
    )
    terms_accepted = BooleanField("I agree to the Terms of Service", validators=[DataRequired()])
    submit = SubmitField("Create My Account")

    def validate_password(self, field):
        result = validate_password_strength(field.data)
        if not result["valid"]:
            raise ValidationError(" ".join(result["errors"]))

    def validate_terms_accepted(self, field):
        if not field.data:
            raise ValidationError("You must accept the Terms of Service to create an account.")


class LoginForm(FlaskForm):
    """Login form for existing users."""

    email = EmailField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember Me", default=False)
    submit = SubmitField("Sign In to ChainWatch Pro")


class ForgotPasswordForm(FlaskForm):
    """Form to request a password reset email."""

    email = EmailField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Send Reset Link")


class ResetPasswordForm(FlaskForm):
    """Form to set a new password after token verification."""

    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Reset Password")

    def validate_new_password(self, field):
        result = validate_password_strength(field.data)
        if not result["valid"]:
            raise ValidationError(" ".join(result["errors"]))


class ForcedPasswordChangeForm(FlaskForm):
    """Form used for mandatory temporary-password replacement on first login."""

    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    change_token = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Set My Password & Continue ->")

    def validate_new_password(self, field):
        result = validate_password_strength(field.data)
        if not result["valid"]:
            raise ValidationError(" ".join(result["errors"]))
