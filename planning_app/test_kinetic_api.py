
"""
Quick smoke-test for the Epicor Kinetic API connection.

Run from the planning_app directory with the venv active:
    python test_kinetic_api.py

Requires a .env file with:
    EPICOR_BASE_URL, EPICOR_USERNAME, EPICOR_PASSWORD, EPICOR_API_KEY
"""

import time
import traceback
from datetime import date

import requests as _requests
from dotenv import load_dotenv

from app.core.epicor_client import KineticClient

load_dotenv()

today = date.today().isoformat()


def test_baq(client, baq_name, params=None, page_size=1):
    """Fetch one record from a BAQ with full timing and URL debug output."""
    url = f"{client.base_url}/api/v2/odata/{client.company}/BaqSvc/{baq_name}/Data"
    paged_url = f"{url}?$top={page_size}&$skip=0"

    print(f"  URL   : {paged_url}")
    print(f"  Params: {params}")

    try:
        t0 = time.time()
        response = client._session.get(paged_url, params=params, timeout=client.timeout)
        elapsed = time.time() - t0

        print(f"  Status: {response.status_code}  ({elapsed:.2f}s)")

        response.raise_for_status()
        data = response.json()
        records = data.get("value", [])
        print(f"  Records returned: {len(records)}")
        if records:
            print(f"  Fields: {list(records[0].keys())}")
        else:
            print("  (no records in response)")

    except _requests.exceptions.Timeout:
        print(f"  TIMEOUT after {client.timeout}s")
    except _requests.exceptions.ConnectionError as e:
        print(f"  CONNECTION ERROR: {e}")
    except _requests.exceptions.HTTPError as e:
        print(f"  HTTP ERROR {e.response.status_code}: {e.response.text[:300]}")
    except Exception:
        traceback.print_exc()


with KineticClient.from_env() as client:

    print("=" * 60)
    print("BAQ: PlanningOutPut (date filtered)")
    print("=" * 60)
    test_baq(client, "PlanningOutPut", params={"DateFrom": today, "DateTo": today})

    print()
    print("=" * 60)
    print("BAQ: PlanningStockReport")
    print("=" * 60)
    test_baq(client, "PlanningStockReport", params={"JobReqByDateSTKPLAN": ""})
