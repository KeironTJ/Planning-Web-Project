"""Forecast preparation records, separate from operational refreshes."""

from datetime import date, datetime, timezone

from app.extensions import db


class ModelCategory(db.Model):
    """User-managed descriptive group, with no built-in forecasting behaviour."""

    __tablename__ = "forecast_model_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    name_key = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    legacy_value = db.Column(db.String(20), nullable=True, unique=True)

    model_classifications = db.relationship(
        "ModelClassification", back_populates="category"
    )


class ModelClassification(db.Model):
    """Assign an exact model name to a user-managed descriptive category.

    Names are trimmed but case-sensitive. Missing decisions remain unclassified,
    rather than implicitly treating every named model as equivalent. This table
    is separate from WorksOrder so COOIS refreshes retain decisions. A category
    is organizational metadata and does not directly control forecast inclusion.
    """

    __tablename__ = "forecast_model_classifications"

    id = db.Column(db.Integer, primary_key=True)
    model = db.Column(db.String(255), nullable=False, unique=True)
    category_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "forecast_model_categories.id",
            ondelete="RESTRICT",
            name="fk_forecast_model_classifications_category_id",
        ),
        nullable=False,
        index=True,
    )
    updated_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    category = db.relationship("ModelCategory", back_populates="model_classifications")


class ForecastDataExtract(db.Model):
    """A saved local VA05 observation, not an original-intake dataset."""

    __tablename__ = "forecast_data_extracts"

    id = db.Column(db.Integer, primary_key=True)
    captured_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    captured_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source = db.Column(db.String(80), nullable=False)
    rules_version = db.Column(db.String(40), nullable=False)
    row_count = db.Column(db.Integer, nullable=False)
    content_hash = db.Column(db.String(64), nullable=False)
    source_settings = db.Column(db.JSON, nullable=False)
    summary = db.Column(db.JSON, nullable=False)

    @property
    def reference(self) -> str:
        environment = self.source_settings.get("capture_environment")
        prefix = {
            "development": "DEV",
            "production": "LIVE",
            "testing": "TEST",
        }.get(environment, "UNLABELLED")
        return f"{prefix}-{self.id}"

    @property
    def capture_identity(self) -> str:
        return f"{self.id}:{self.captured_at.isoformat()}"

    @property
    def order_period(self) -> tuple[date, date] | None:
        if "order_date_from" not in self.source_settings:
            return None
        return (
            date.fromisoformat(self.source_settings["order_date_from"]),
            date.fromisoformat(self.source_settings["order_date_to"]),
        )


class ForecastDataNote(db.Model):
    """Append-only commentary about a saved extract."""

    __tablename__ = "forecast_data_notes"

    id = db.Column(db.Integer, primary_key=True)
    extract_id = db.Column(
        db.Integer,
        db.ForeignKey("forecast_data_extracts.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    body = db.Column(db.Text, nullable=False)
    author_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    author = db.relationship("User", lazy="joined")


class ForecastDataRecord(db.Model):
    """Whitelisted observed values and findings, preserved across refreshes."""

    __tablename__ = "forecast_data_records"
    __table_args__ = (
        db.UniqueConstraint(
            "extract_id", "source_row_id",
            name="uq_forecast_extract_source_row",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    extract_id = db.Column(
        db.Integer,
        db.ForeignKey("forecast_data_extracts.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    source_row_id = db.Column(db.Integer, nullable=False)
    data = db.Column(db.JSON, nullable=False)
    findings = db.Column(db.JSON, nullable=False)
    has_findings = db.Column(db.Boolean, nullable=False)

    decisions = db.relationship(
        "ForecastDataDecision", order_by="ForecastDataDecision.id.desc()",
        lazy="selectin",
    )


class ForecastDataDecision(db.Model):
    """Append-only evidence; never changes or excludes source rows."""

    __tablename__ = "forecast_data_decisions"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('investigate', 'accepted', 'source_correction')",
            name="ck_forecast_data_decision_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_id = db.Column(
        db.Integer,
        db.ForeignKey("forecast_data_records.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    status = db.Column(db.String(30), nullable=False)
    rationale = db.Column(db.Text, nullable=False)
    reviewed_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
