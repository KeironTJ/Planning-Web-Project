"""
Admin domain models.

Site:          represents a physical business site (factory/warehouse).
               All operational data (orders, capacity, materials) is scoped to a site.
SystemSetting: a simple key/value store for administrator-controlled
               application behaviour toggles and configuration values.
"""

from datetime import datetime, timezone

from app.extensions import db


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------

class Site(db.Model):
    """
    A physical business site belonging to the group.

    Every piece of operational data (departments, orders, capacity, materials)
    is scoped to a site via a site_id foreign key.  Users can be granted access
    to one or more sites; the active site is stored in their session.
    """

    __tablename__ = "sites"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Site {self.code}: {self.name}>"


class SystemSetting(db.Model):
    """
    Application-wide key/value settings, editable by admins.

    Values are stored as strings; use the typed class-method helpers to
    read boolean or integer settings.

    Seed defaults are inserted at first access via get() — no migration
    data required.
    """

    __tablename__ = "system_settings"

    key   = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500), nullable=False, default="")
    description = db.Column(db.String(500), nullable=True)

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        """Return the raw string value for *key*, or *default* if absent."""
        row = cls.query.get(key)
        return row.value if row is not None else default

    @classmethod
    def get_bool(cls, key: str, default: bool = False) -> bool:
        """Return the value as a boolean (stored as '1'/'0')."""
        raw = cls.get(key, "1" if default else "0")
        return raw == "1"

    @classmethod
    def set(cls, key: str, value: str, description: str | None = None) -> None:
        """Upsert a setting.  Caller is responsible for db.session.commit()."""
        row = cls.query.get(key)
        if row is None:
            row = cls(key=key, value=value, description=description)
            db.session.add(row)
        else:
            row.value = value
            if description is not None:
                row.description = description

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        """Return the value as an integer."""
        try:
            return int(cls.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    @classmethod
    def set_bool(cls, key: str, flag: bool, description: str | None = None) -> None:
        """Convenience wrapper — stores True as '1', False as '0'."""
        cls.set(key, "1" if flag else "0", description)

    def __repr__(self) -> str:
        return f"<SystemSetting {self.key}={self.value!r}>"


# ---------------------------------------------------------------------------
# Setting keys (constants so callers never mistype a key)
# ---------------------------------------------------------------------------

SETTING_AUTO_COMPLETE_DESPATCH = "auto_complete_despatch"
SETTING_DAILY_OUTPUT_TARGET = "daily_output_target"
SETTING_DAILY_OUTPUT_TARGET_DAYS = "daily_output_target_days"  # comma-separated weekday ints 0=Mon…4=Fri
SETTING_MRP_LEAD_DAYS = "mrp_material_lead_days"  # days material must arrive before ship date


# ---------------------------------------------------------------------------
# SyncSchedule
# ---------------------------------------------------------------------------

class SyncSchedule(db.Model):
    """
    Per-importer schedule configuration for the automated Epicor sync.

    One row per REGISTRY key.  The background scheduler tick checks this table
    every minute and fires any importers whose next_run_at has passed.
    """

    __tablename__ = "sync_schedules"

    STATUS_SUCCESS = "success"
    STATUS_FAILED  = "failed"
    STATUS_RUNNING = "running"

    id              = db.Column(db.Integer, primary_key=True)
    importer_key    = db.Column(db.String(50), unique=True, nullable=False, index=True)
    enabled         = db.Column(db.Boolean, default=False, nullable=False)
    interval_minutes= db.Column(db.Integer, default=120, nullable=False)
    # Optional JSON string of params to pass to the importer at run time.
    # Use the sentinel value "__today__" for any param value that should be
    # replaced with today's date (YYYY-MM-DD) at the moment the job fires.
    # A NULL / empty value means "use the importer's own default logic".
    schedule_params = db.Column(db.Text, nullable=True)
    last_run_at     = db.Column(db.DateTime(timezone=True), nullable=True)
    next_run_at     = db.Column(db.DateTime(timezone=True), nullable=True)
    last_status     = db.Column(db.String(20), nullable=True)
    last_row_count  = db.Column(db.Integer, nullable=True)
    last_error      = db.Column(db.Text, nullable=True)
    is_running      = db.Column(db.Boolean, default=False, nullable=False)
    updated_at      = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def display_name(self) -> str:
        return self.importer_key.replace("_", " ").title()

    @property
    def parsed_params(self) -> dict:
        """Return schedule_params as a dict, or {} if unset / invalid JSON."""
        import json
        if not self.schedule_params:
            return {}
        try:
            return json.loads(self.schedule_params)
        except (ValueError, TypeError):
            return {}

    def resolved_params(self) -> dict | None:
        """
        Return the params dict to pass to the importer at run time.

        Replaces the sentinel string ``"__today__"`` with today's ISO date.
        Returns ``None`` when no params are configured (use importer defaults).
        """
        from datetime import date
        raw = self.parsed_params
        if not raw:
            return None
        today = date.today().isoformat()
        return {k: (today if v == "__today__" else v) for k, v in raw.items()}

    def schedule_next_run(self) -> None:
        """Compute and set next_run_at from now based on interval_minutes."""
        from datetime import timedelta
        self.next_run_at = datetime.now(timezone.utc) + timedelta(minutes=self.interval_minutes)

    def __repr__(self) -> str:
        return f"<SyncSchedule {self.importer_key} every={self.interval_minutes}m enabled={self.enabled}>"


# ---------------------------------------------------------------------------
# SyncJob  /  SyncJobItem  — grouped, ordered sync routines
# ---------------------------------------------------------------------------

class SyncJob(db.Model):
    """
    A named routine that runs a set of importers in sequence on a schedule.

    Examples:
      "Sync All"          — stock, POs, materials, works orders, sales every 2 h
      "Production Output" — production_output every 5 min
    """

    __tablename__ = "sync_jobs"

    STATUS_SUCCESS = "success"
    STATUS_FAILED  = "failed"
    STATUS_PARTIAL = "partial"   # some items succeeded, some failed
    STATUS_RUNNING = "running"

    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(100), nullable=False)
    enabled          = db.Column(db.Boolean, default=False, nullable=False)
    interval_minutes = db.Column(db.Integer, default=120, nullable=False)
    last_run_at      = db.Column(db.DateTime(timezone=True), nullable=True)
    next_run_at      = db.Column(db.DateTime(timezone=True), nullable=True)
    last_status      = db.Column(db.String(20), nullable=True)
    is_running       = db.Column(db.Boolean, default=False, nullable=False)
    created_at       = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    items = db.relationship(
        "SyncJobItem",
        order_by="SyncJobItem.sort_order",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def schedule_next_run(self) -> None:
        from datetime import timedelta
        self.next_run_at = datetime.now(timezone.utc) + timedelta(minutes=self.interval_minutes)

    @property
    def interval_display(self) -> str:
        h, m = divmod(self.interval_minutes, 60)
        if h and m:
            return f"{h}h {m}m"
        if h:
            return f"{h} hr"
        return f"{self.interval_minutes} min"

    def __repr__(self) -> str:
        return f"<SyncJob {self.id}: {self.name!r} every={self.interval_minutes}m enabled={self.enabled}>"


class SyncJobItem(db.Model):
    """
    One importer step within a SyncJob, with optional date-param overrides.

    Items within a job are executed in ascending sort_order.
    """

    __tablename__ = "sync_job_items"

    STATUS_SUCCESS = "success"
    STATUS_FAILED  = "failed"

    id              = db.Column(db.Integer, primary_key=True)
    job_id          = db.Column(
        db.Integer,
        db.ForeignKey("sync_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    importer_key    = db.Column(db.String(50), nullable=False)
    sort_order      = db.Column(db.Integer, default=0, nullable=False)
    # Optional JSON params to pass to the importer at run time.
    # "__today__" as a value is replaced with today's ISO date when the job fires.
    schedule_params = db.Column(db.Text, nullable=True)
    last_status     = db.Column(db.String(20), nullable=True)
    last_row_count  = db.Column(db.Integer, nullable=True)
    last_error      = db.Column(db.Text, nullable=True)
    last_run_at     = db.Column(db.DateTime(timezone=True), nullable=True)

    job = db.relationship("SyncJob", back_populates="items")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def parsed_params(self) -> dict:
        import json
        if not self.schedule_params:
            return {}
        try:
            return json.loads(self.schedule_params)
        except (ValueError, TypeError):
            return {}

    def resolved_params(self) -> dict | None:
        """Return params with __today__ replaced by today's ISO date, or None."""
        from datetime import date
        raw = self.parsed_params
        if not raw:
            return None
        today = date.today().isoformat()
        return {k: (today if v == "__today__" else v) for k, v in raw.items()}

    @property
    def display_name(self) -> str:
        return self.importer_key.replace("_", " ").title()

    @property
    def params_label(self) -> str:
        pp = self.parsed_params
        if not pp:
            return "Auto"
        mode = pp.get("mode")
        if mode == "today":
            return "Today only"
        if mode == "range":
            f = pp.get("DateFrom") or pp.get("OrderDateFrom", "")
            t = pp.get("DateTo")   or pp.get("OrderDateTo",   "")
            return f"{f} → {t}"
        return "Custom"

    def __repr__(self) -> str:
        return f"<SyncJobItem job={self.job_id} key={self.importer_key!r} order={self.sort_order}>"
