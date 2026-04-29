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
    def set_bool(cls, key: str, flag: bool, description: str | None = None) -> None:
        """Convenience wrapper — stores True as '1', False as '0'."""
        cls.set(key, "1" if flag else "0", description)

    def __repr__(self) -> str:
        return f"<SystemSetting {self.key}={self.value!r}>"


# ---------------------------------------------------------------------------
# Setting keys (constants so callers never mistype a key)
# ---------------------------------------------------------------------------

SETTING_AUTO_COMPLETE_DESPATCH = "auto_complete_despatch"
