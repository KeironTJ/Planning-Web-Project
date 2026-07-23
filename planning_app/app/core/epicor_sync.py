"""
Base class for Epicor BAQ → database sync importers.

Every BAQ that feeds the database follows the same lifecycle:
  1. Open a KineticClient session
  2. Create an ImportBatch (audit trail)
  3. Fetch all records from the BAQ (auto-paginated)
  4. Truncate the target table, reload from the fetched records
  5. Commit and mark the batch SUCCESS — or rollback and mark FAILED

To add a new BAQ, subclass EpicorBaqImporter and implement _sync_records().

-------------------------------------------------------------------------------
Example — adding a new BAQ importer:

    # app/my_module/importers.py

    from app.core.epicor_sync import EpicorBaqImporter
    from app.orders.models import ImportBatch
    from app.extensions import db
    from .models import MyModel   # <-- your new model

    class MyBaqImporter(EpicorBaqImporter):
        BAQ_NAME = "MyEpicorBaqId"          # exact BAQ name in Epicor
        IMPORT_TYPE = "my_baq"              # add a matching constant to ImportBatch
        BAQ_PARAMS = {}                     # static params (date filters etc.)
                                            # pass dynamic params to .run(params=...)

        def _sync_records(self, records, batch, now):
            MyModel.query.delete()
            db.session.flush()

            for r in records:
                db.session.add(MyModel(
                    # Map BAQ field names directly — they drive the schema
                    product_code = r["FieldName_From_BAQ"],
                    description  = r["AnotherBAQField"],
                    imported_at  = now,
                ))
            batch.rows_inserted = len(records)

-------------------------------------------------------------------------------
Triggering a sync (admin route, CLI command, or scheduled task):

    from app.core.epicor_sync import MyBaqImporter
    from app.core.epicor_client import KineticClient
    from flask import current_app

    with KineticClient.from_app(current_app._get_current_object()) as client:
        batch = MyBaqImporter(client).run()

-------------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.extensions import db

logger = logging.getLogger(__name__)


class EpicorBaqImporter:
    """
    Abstract base for BAQ → database importers.

    Subclasses must set BAQ_NAME and IMPORT_TYPE, and implement _sync_records().
    """

    #: The exact BAQ ID as configured in Epicor (override in subclass)
    BAQ_NAME: str = ""

    #: ImportBatch.import_type constant for this importer (override in subclass)
    IMPORT_TYPE: str = ""

    #: Static BAQ filter params applied on every run.
    #: Dynamic params (e.g. date range) can be passed to .run(params={...}).
    BAQ_PARAMS: dict = {}

    #: Override in subclasses whose BAQ is slow with large pages (complex joins).
    #: None = use the KineticClient default (500 rows/page).
    PAGE_SIZE: int | None = None

    #: Split date-range BAQ calls into slices of this many days to avoid
    #: Epicor applying ``$skip`` before the ``DateFrom``/``DateTo`` filter,
    #: which causes missing rows when a large filtered result spans multiple
    #: pages.  ``None`` (default) disables chunking.
    CHUNK_DAYS: int | None = None

    #: Set to True in incremental importers that legitimately fetch 0 records
    #: (e.g. when the date window contains no new data).  Bypasses the
    #: zero-record safety guard that normally prevents accidental table wipes.
    ALLOW_EMPTY_RESULT: bool = False

    def __init__(self, client) -> None:
        """
        Args:
            client: An open KineticClient instance.  The caller is responsible
                    for the client lifecycle (open / close / context manager).
        """
        if not self.BAQ_NAME:
            raise NotImplementedError(f"{type(self).__name__} must define BAQ_NAME")
        if not self.IMPORT_TYPE:
            raise NotImplementedError(f"{type(self).__name__} must define IMPORT_TYPE")
        self._client = client

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, params: dict | None = None, triggered_by_id: int | None = None):
        """
        Execute the full sync: fetch BAQ → truncate → reload → commit.

        Args:
            params:           Additional / override BAQ filter params for this run.
            triggered_by_id:  User.id who triggered the sync (for the audit log).

        Returns:
            The completed ImportBatch record.
        """
        from app.sales.orders.models import ImportBatch  # lazy — avoids circular import at startup
        merged_params = {
            **self.BAQ_PARAMS,
            **self.get_dynamic_params(),
            **(params or {}),
        }

        batch = ImportBatch(
            import_type=self.IMPORT_TYPE,
            filename=f"epicor_api:{self.BAQ_NAME}",  # signals API origin vs CSV upload
            uploaded_by_id=triggered_by_id,
            status=ImportBatch.STATUS_PENDING,
        )
        db.session.add(batch)
        db.session.flush()  # get batch.id without committing

        now = datetime.now(timezone.utc)

        try:
            logger.info("EpicorSync starting  BAQ=%s  batch_id=%d", self.BAQ_NAME, batch.id)

            records = self._fetch_records(merged_params)
            batch.row_count = len(records)

            # Guard against a silent empty response wiping the table.
            # If the BAQ has never returned data before, allow 0 records (first run).
            # If we have existing rows and the API returns 0, treat it as a failure.
            if len(records) == 0 and not self.ALLOW_EMPTY_RESULT:
                from app.extensions import db as _db
                existing = _db.session.execute(
                    _db.text(f"SELECT COUNT(*) FROM {self._target_table()}")
                ).scalar()
                if existing and existing > 0:
                    raise RuntimeError(
                        f"BAQ {self.BAQ_NAME!r} returned 0 records but the table has "
                        f"{existing} existing rows — aborting to prevent data loss. "
                        "Check network connectivity and retry."
                    )

            logger.info("EpicorSync fetched %d records  BAQ=%s", len(records), self.BAQ_NAME)

            # Store merged params so _sync_records can use the requested date
            # range for deletion rather than deriving it from returned records.
            self._last_merged_params = merged_params
            self._sync_records(records, batch, now)

            batch.status = ImportBatch.STATUS_SUCCESS
            db.session.commit()

            logger.info(
                "EpicorSync complete  BAQ=%s  batch_id=%d  inserted=%s  updated=%s",
                self.BAQ_NAME, batch.id, batch.rows_inserted, batch.rows_updated,
            )

        except Exception as exc:
            db.session.rollback()
            batch.status = ImportBatch.STATUS_FAILED
            batch.error_message = str(exc)
            logger.exception("EpicorSync failed  BAQ=%s  error=%s", self.BAQ_NAME, exc)
            try:
                db.session.add(batch)
                db.session.commit()
            except Exception:
                db.session.rollback()
            raise

        return batch

    def _target_table(self) -> str:
        """Return the DB table name for the guard check. Override if needed."""
        # Derive from IMPORT_TYPE by default (e.g. "epicor_stock" → not reliable).
        # Subclasses should override this if the default derivation is wrong.
        raise NotImplementedError(f"{type(self).__name__} must implement _target_table()")

    def _fetch_records(self, merged_params: dict) -> list[dict]:
        """
        Fetch all records from the BAQ for the given merged params.

        If ``CHUNK_DAYS`` is set and ``DateFrom`` / ``DateTo`` are present in
        *merged_params*, the range is split into slices of that many days so
        that each BAQ call fits within a single page (``$skip=0`` on every
        call).  This avoids Epicor applying ``$skip`` before the date filter,
        which returns wrong rows when a large filtered result spans pages.

        Subclasses using different date-param names should override this method.
        """
        from_str = (merged_params or {}).get('DateFrom')
        to_str   = (merged_params or {}).get('DateTo')

        if self.CHUNK_DAYS and from_str and to_str:
            from datetime import date, timedelta
            try:
                from_d = date.fromisoformat(from_str)
                to_d   = date.fromisoformat(to_str)
            except ValueError:
                pass  # malformed dates — fall through to standard single fetch
            else:
                all_records: list[dict] = []
                chunk_start = from_d
                while chunk_start <= to_d:
                    chunk_end = min(chunk_start + timedelta(days=self.CHUNK_DAYS - 1), to_d)
                    chunk_params = {
                        **merged_params,
                        'DateFrom': chunk_start.isoformat(),
                        'DateTo':   chunk_end.isoformat(),
                    }
                    records = self._client.get_baq(
                        self.BAQ_NAME,
                        params=chunk_params,
                        page_size=self.PAGE_SIZE,
                    )
                    logger.info(
                        "%s chunk %s \u2192 %s  records=%d",
                        type(self).__name__, chunk_start, chunk_end, len(records),
                    )
                    all_records.extend(records)
                    chunk_start = chunk_end + timedelta(days=1)
                return all_records

        return self._client.get_baq(
            self.BAQ_NAME,
            params=merged_params or None,
            page_size=self.PAGE_SIZE,
        )

    def get_dynamic_params(self) -> dict:
        """
        Return runtime-computed BAQ parameters merged with BAQ_PARAMS on every run.

        Override in subclasses that need the current date or other runtime values.
        Default returns an empty dict (no extra params).
        """
        return {}

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def _sync_records(self, records: list[dict], batch: ImportBatch, now: datetime) -> None:
        """
        Write ``records`` to the database.

        Called inside an open transaction — do NOT commit here.
        Update ``batch.rows_inserted`` / ``batch.rows_updated`` as appropriate.

        Args:
            records: All records fetched from the BAQ (flat list of dicts).
            batch:   The active ImportBatch — update row counts here.
            now:     UTC timestamp to stamp on every inserted row.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement _sync_records()")
