"""WTForms for the capacity planning module."""

from flask_wtf import FlaskForm
from wtforms import (
    StringField, TextAreaField, IntegerField, DecimalField,
    DateField, SelectField, SubmitField,
)
from wtforms.validators import DataRequired, Optional, Length, NumberRange
from .models import WorkOrder


class WorkCentreForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), Length(max=20)])
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    department = StringField("Department", validators=[Optional(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional()])
    hours_per_shift = DecimalField("Hours per Shift", places=2, default=8.0, validators=[NumberRange(min=0.5, max=24)])
    shifts_per_day = IntegerField("Shifts per Day", default=1, validators=[NumberRange(min=1, max=3)])
    efficiency_pct = DecimalField("Efficiency %", places=2, default=85.0, validators=[NumberRange(min=1, max=100)])
    submit = SubmitField("Save")


class WorkOrderForm(FlaskForm):
    order_number = StringField("Order Number", validators=[DataRequired(), Length(max=30)])
    product_code = StringField("Product Code", validators=[DataRequired(), Length(max=50)])
    product_description = StringField("Product Description", validators=[Optional(), Length(max=200)])
    quantity = DecimalField("Quantity", places=2, validators=[DataRequired(), NumberRange(min=0.01)])
    priority = IntegerField("Priority (1=High, 100=Low)", default=50, validators=[NumberRange(min=1, max=100)])
    planned_start = DateField("Planned Start", validators=[DataRequired()])
    planned_end = DateField("Planned End", validators=[DataRequired()])
    routing_id = SelectField("Routing", coerce=int, validators=[Optional()])
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Save")


class GenerateBucketsForm(FlaskForm):
    work_centre_id = SelectField("Work Centre", coerce=int, validators=[DataRequired()])
    from_date = DateField("From Date", validators=[DataRequired()])
    weeks = IntegerField("Number of Weeks", default=13, validators=[NumberRange(min=1, max=52)])
    submit = SubmitField("Generate Buckets")
