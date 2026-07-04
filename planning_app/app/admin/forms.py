"""Admin blueprint forms."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import SelectField, DecimalField, IntegerField, BooleanField
from wtforms.validators import Optional, NumberRange


class ImportUploadForm(FlaskForm):
    import_type = SelectField(
        "Import Type",
        choices=[
            ("sales",          "Sales Orders (sales_HIDE.csv)"),
            ("coois",          "Production / Works Orders (COOIS_HIDE.csv)"),
            ("stock",          "Stock On Hand (SOH_HIDE.csv)"),
            ("open_po",        "Open Purchase Orders (OpenPO_HIDE.csv)"),
            ("main_material",  "Material Requirements (MatReq_HIDE.csv)"),
            ("labour_plan",    "Labour Plan (Capacity)"),
            ("oob",            "Open Order Book (legacy OOB format)"),
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
    daily_output_target = IntegerField(
        "Daily Target (units)",
        validators=[Optional(), NumberRange(min=0)],
    )
    daily_target_mon = BooleanField("Mon")
    daily_target_tue = BooleanField("Tue")
    daily_target_wed = BooleanField("Wed")
    daily_target_thu = BooleanField("Thu")
    daily_target_fri = BooleanField("Fri")


class DeptHoursForm(FlaskForm):
    target_hours_per_day = DecimalField(
        "Target Hours / Day",
        places=2,
        validators=[Optional()],
    )
    flow_order = IntegerField(
        "Flow Order",
        validators=[Optional(), NumberRange(min=1, max=999)],
    )
    track = BooleanField("Track Department")
