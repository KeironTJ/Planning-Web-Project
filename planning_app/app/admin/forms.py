"""Admin blueprint forms."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import SelectField, HiddenField, DecimalField, IntegerField, BooleanField
from wtforms.validators import Optional, NumberRange


class ImportUploadForm(FlaskForm):
    import_type = SelectField(
        "Import Type",
        choices=[
            ("oob",             "Open Order Book (OOB)"),
            ("stock",           "Stock On Hand"),
            ("open_po",         "Open Purchase Orders"),
            ("main_material",   "Material Requirements — Main Line"),
            ("as_material",     "Material Requirements — After Sales"),
            ("labour_plan",     "Labour Plan (Capacity)"),
            ("smv",             "SMV Table"),
            ("production_flow", "Production Flow Lead Times"),
        ],
    )
    file = FileField(
        "CSV File",
        validators=[
            FileRequired(),
            FileAllowed(["csv"], "CSV files only"),
        ],
    )


class SystemSettingsForm(FlaskForm):
    auto_complete_despatch = BooleanField(
        "Auto-complete Despatch",
        description=(
            "When all non-Despatch operations for an order line are completed, "
            "automatically mark the Despatch operation as completed too."
        ),
    )


class DeptHoursForm(FlaskForm):
    target_hours_per_day = DecimalField(
        "Target Hours / Day",
        places=2,
        validators=[Optional()],
    )
    default_lead_time_days = IntegerField(
        "Default Lead Time (working days)",
        default=2,
        validators=[Optional(), NumberRange(min=0, max=30)],
    )
    flow_order = IntegerField(
        "Flow Order",
        validators=[Optional(), NumberRange(min=1, max=999)],
    )
