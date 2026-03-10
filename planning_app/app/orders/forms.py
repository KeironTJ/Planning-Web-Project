"""Forms for the orders / WIP tracker blueprint."""

from flask_wtf import FlaskForm
from wtforms import SelectField, DateField, TextAreaField, HiddenField
from wtforms.validators import Optional, DataRequired

from .models import WorksOrderOperation

_STATUS_CHOICES = [
    (s, WorksOrderOperation.STATUS_META[s][0])
    for s in WorksOrderOperation.VALID_STATUSES
]


class OperationStatusForm(FlaskForm):
    """Inline form to update a single operation's planner fields."""
    status      = SelectField("Status", choices=_STATUS_CHOICES, validators=[DataRequired()])
    planned_date = DateField("Planned Date", validators=[Optional()])
    notes       = TextAreaField("Notes", validators=[Optional()])


class BulkStatusForm(FlaskForm):
    """Bulk status update — list of operation IDs + target status."""
    status       = SelectField("New Status", choices=_STATUS_CHOICES, validators=[DataRequired()])
    operation_ids = HiddenField("Operation IDs", validators=[DataRequired()])
