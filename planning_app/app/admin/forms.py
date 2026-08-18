"""Admin blueprint forms."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import SelectField, DecimalField, IntegerField, BooleanField, StringField, TextAreaField
from wtforms.validators import Optional, NumberRange, DataRequired, Length


class ImportUploadForm(FlaskForm):
    import_type = SelectField(
        "Import Type",
        choices=[
            ("stock",          "Stock On Hand (SOH_HIDE.csv)"),
            ("open_po",        "Open Purchase Orders (OpenPO_HIDE.csv)"),
            ("main_material",  "Material Requirements (MatReq_HIDE.csv)"),
            ("labour_plan",    "Labour Plan (Capacity)"),
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
    mrp_lead_days = IntegerField(
        "Fabric/Hide Lead Days",
        validators=[Optional(), NumberRange(min=0, max=90)],
        description="Days before ship date a PO must arrive to count as fabric/hide coverage.",
    )
    mrp_component_lead_days = IntegerField(
        "Component Lead Days",
        validators=[Optional(), NumberRange(min=0, max=90)],
        description="Days before ship date a PO must arrive to count as component coverage.",
    )
    fabric_class_ids = StringField(
        "Fabric/Hide Class IDs",
        validators=[Optional(), Length(max=200)],
        description="Comma-separated Epicor class IDs included in fabric/hide availability (e.g. A101,A102,A105,B101,C101,Z102).",
    )
    component_class_ids = StringField(
        "Component Class IDs",
        validators=[Optional(), Length(max=500)],
        description="Comma-separated class IDs included in component availability. Leave blank to include all classes from PlanningMatReqComp.",
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
    op_code = StringField(
        "Epicor Op Code",
        validators=[Optional(), Length(max=50)],
        description="The Epicor next_op code that maps to this department (e.g. SEW, FRAME, BATCH).",
    )
    track = BooleanField("Track Department")


class DeptCreateForm(FlaskForm):
    code = StringField(
        "Code",
        validators=[DataRequired(), Length(max=50)],
        description="Short unique identifier, e.g. CUTTING or DESPATCH.",
    )
    name = StringField(
        "Name",
        validators=[DataRequired(), Length(max=100)],
    )
    target_hours_per_day = DecimalField(
        "Target Hours / Day",
        places=2,
        validators=[Optional()],
    )
    flow_order = IntegerField(
        "Flow Order",
        validators=[Optional(), NumberRange(min=1, max=999)],
    )
    op_code = StringField(
        "Epicor Op Code",
        validators=[Optional(), Length(max=50)],
        description="The Epicor next_op code that maps to this department (e.g. SEW, FRAME, BATCH).",
    )
    track = BooleanField("Track Department")
