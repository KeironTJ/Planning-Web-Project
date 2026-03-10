"""Admin blueprint forms."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import SelectField, HiddenField, DecimalField
from wtforms.validators import Optional


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


class DeptHoursForm(FlaskForm):
    target_hours_per_day = DecimalField(
        "Target Hours / Day",
        places=2,
        validators=[Optional()],
    )
