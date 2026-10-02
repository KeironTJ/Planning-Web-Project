"""Network fetches must not hold the database's write lock."""

from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.sales.orders.models import ImportBatch
from app.transport.manifest_importer import TransportManifestImporter
from app.transport.models import TransportManifest
from tests.test_transport_manifests import manifest_row


@pytest.mark.parametrize("fetch_fails", [False, True])
def test_pending_audit_is_visible_and_another_writer_can_run_during_fetch(tmp_path, fetch_fails):
    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "sync.db")
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 0.1}}

    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
        client = Mock()

        def fetch(*args, **kwargs):
            with db.engine.begin() as connection:
                pending = connection.execute(sa.text(
                    "SELECT id, status FROM import_batches WHERE import_type = :kind"
                ), {"kind": TransportManifestImporter.IMPORT_TYPE}).one()
                assert pending.status == "pending"
                connection.execute(sa.text(
                    "UPDATE import_batches SET notes = 'Other writer succeeded' WHERE id = :id"
                ), {"id": pending.id})
            if fetch_fails:
                raise RuntimeError("Epicor unavailable")
            return [manifest_row()]

        client.get_baq.side_effect = fetch
        try:
            if fetch_fails:
                with pytest.raises(RuntimeError, match="Epicor unavailable"):
                    TransportManifestImporter(client).run(params={"DateFrom": "2026-10-01", "DateBefore": "2026-10-02"})
            else:
                TransportManifestImporter(client).run(params={"DateFrom": "2026-10-01", "DateBefore": "2026-10-02"})
            batch = ImportBatch.query.one()
            assert batch.status == ("failed" if fetch_fails else "success")
            assert TransportManifest.query.count() == (0 if fetch_fails else 1)
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
