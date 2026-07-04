"""
Epicor Kinetic REST API client.

Wraps the OData v2 BAQ endpoint with:
- Persistent requests.Session (connection pooling, shared auth/headers)
- Both x-api-key header AND HTTP Basic Auth — required by this Epicor instance
- Automatic pagination ($top / $skip) — Epicor's default page cap is 1000 rows
- Retry with exponential backoff for transient server errors (429, 5xx)
- Configurable SSL verification (set EPICOR_VERIFY_SSL=true once cert is sorted)

Standalone / script usage:
    from app.core.epicor_client import KineticClient
    with KineticClient.from_env() as client:
        rows = client.get_baq("PlanningOutPut", {"DateFrom": "2026-07-01"})

Inside a Flask app context:
    from app.core.epicor_client import KineticClient
    from flask import current_app
    with KineticClient.from_app(current_app._get_current_object()) as client:
        rows = client.get_baq("StockOnHand")
"""

from __future__ import annotations

import logging
import os

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class KineticClientError(Exception):
    """Raised for non-retryable or configuration errors from the Epicor client."""


class KineticClient:
    """
    Thin, session-based wrapper around the Epicor Kinetic OData v2 BAQ endpoint.

    Both an ``x-api-key`` header AND HTTP Basic Auth are required by this
    Epicor instance — the client sends both on every request.
    """

    # Epicor's hard OData page cap is typically 1000 rows.
    # 500 keeps individual responses fast and gives headroom for large result sets.
    DEFAULT_PAGE_SIZE: int = 500

    def __init__(
        self,
        base_url: str,
        company: str,
        username: str,
        password: str,
        api_key: str,
        verify_ssl: bool = False,
        page_size: int = DEFAULT_PAGE_SIZE,
        timeout: tuple = (10, 60),  # (connect timeout, read timeout) in seconds
    ) -> None:
        if not all([base_url, company, username, password, api_key]):
            raise KineticClientError(
                "All Epicor connection parameters (base_url, company, username, "
                "password, api_key) are required."
            )

        self.base_url = base_url.rstrip("/")
        self.company = company
        self.page_size = page_size
        self.timeout = timeout

        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(username, password)
        self._session.headers.update({
            "x-api-key": api_key,
            "Accept": "application/json",
        })
        self._session.verify = verify_ssl

        if not verify_ssl:
            # Suppress the InsecureRequestWarning — expected until SSL cert is provisioned
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Retry on transient server errors only. 4xx errors are caller mistakes
        # and should surface immediately rather than being retried.
        retry = Retry(
            total=3,
            backoff_factor=1,       # retry waits: 0 s, 1 s, 2 s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,  # let raise_for_status() handle it after retries
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, **kwargs) -> KineticClient:
        """
        Build a client directly from environment variables.

        Required env vars: EPICOR_BASE_URL, EPICOR_USERNAME, EPICOR_PASSWORD,
        EPICOR_API_KEY.  Optional: EPICOR_COMPANY (default "TET01"),
        EPICOR_VERIFY_SSL (default "false").

        Intended for scripts, management commands, and tests.
        """
        return cls(
            base_url=os.environ["EPICOR_BASE_URL"],
            company=os.environ.get("EPICOR_COMPANY", "TET01"),
            username=os.environ["EPICOR_USERNAME"],
            password=os.environ["EPICOR_PASSWORD"],
            api_key=os.environ["EPICOR_API_KEY"],
            verify_ssl=os.environ.get("EPICOR_VERIFY_SSL", "false").lower() == "true",
            **kwargs,
        )

    @classmethod
    def from_app(cls, app, **kwargs) -> KineticClient:
        """
        Build a client from a Flask app's config.

        Use this inside a Flask app context so that config is loaded from
        the app rather than raw environment variables.
        """
        return cls(
            base_url=app.config["EPICOR_BASE_URL"],
            company=app.config.get("EPICOR_COMPANY", "TET01"),
            username=app.config["EPICOR_USERNAME"],
            password=app.config["EPICOR_PASSWORD"],
            api_key=app.config["EPICOR_API_KEY"],
            verify_ssl=app.config.get("EPICOR_VERIFY_SSL", False),
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def get_baq(
        self,
        baq_name: str,
        params: dict | None = None,
        page_size: int | None = None,
    ) -> list[dict]:
        """
        Fetch ALL records from a BAQ, automatically handling pagination.

        BAQ-specific filter parameters (e.g. ``DateFrom``, ``DateTo``) are
        passed via ``params``.  OData system parameters (``$top``, ``$skip``)
        are managed internally — do not include them in ``params``.

        Args:
            baq_name:  The Epicor BAQ ID (e.g. ``"PlanningOutPut"``).
            params:    Optional dict of BAQ filter parameters.
            page_size: Override the client's default page size for this call.
                       Pass ``1`` to fetch a single record (e.g. for field inspection).

        Returns:
            A flat list of record dicts exactly as returned by Epicor.

        Raises:
            requests.HTTPError: On non-2xx responses after retries are exhausted.
        """
        url = f"{self.base_url}/api/v2/odata/{self.company}/BaqSvc/{baq_name}/Data"
        effective_page_size = page_size if page_size is not None else self.page_size
        all_records: list = []
        skip = 0

        while True:
            # OData system params ($top, $skip) must stay as literal dollar signs —
            # requests would percent-encode them to %24top which Epicor ignores,
            # causing it to return the full unfiltered dataset.
            # Embed them directly in the URL; requests appends the BAQ filter params.
            paged_url = f"{url}?$top={effective_page_size}&$skip={skip}"

            logger.debug(
                "KineticClient GET %s  $top=%d  $skip=%d", baq_name, effective_page_size, skip
            )

            response = self._session.get(paged_url, params=params, timeout=self.timeout)
            response.raise_for_status()

            payload = response.json()
            records: list = payload.get("value", [])

            # Some BAQs ignore $top and return the full result set regardless.
            # Detect this: if we got MORE records than we asked for, the BAQ doesn't
            # support server-side pagination — take all returned records and stop.
            if len(records) > effective_page_size:
                logger.info(
                    "KineticClient BAQ=%s returned %d records for $top=%d — "
                    "BAQ ignores pagination, treating as non-paginating",
                    baq_name, len(records), effective_page_size,
                )
                all_records.extend(records)
                break

            all_records.extend(records)

            logger.info(
                "KineticClient BAQ=%s  page_records=%d  total=%d  skip=%d",
                baq_name, len(records), len(all_records), skip,
            )

            # Fewer records than page size means this was the last (or only) page
            if len(records) < effective_page_size:
                break

            skip += effective_page_size

        return all_records

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the underlying session and its connection pool."""
        self._session.close()

    def __enter__(self) -> KineticClient:
        return self

    def __exit__(self, *_) -> None:
        self.close()
