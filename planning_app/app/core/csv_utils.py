"""
CSV import utilities.

Shared helpers used by all CSV importers:
- Excel serial date conversion (all ERP date fields are integers)
- CSV file reading with UTF-8 BOM handling
- Safe type coercions for CSV string values
"""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional, Iterator


# The Excel date epoch is 1899-12-30 (not 1900-01-01 due to the Lotus 1-2-3
# leap year bug that Excel inherited).
_EXCEL_EPOCH = date(1899, 12, 30)


def excel_serial_to_date(value) -> Optional[date]:
    """
    Convert an Excel date serial number to a Python date.

    Excel stores dates as integers counting days since 1899-12-30.
    Returns None for any value that cannot be converted (empty, None, non-numeric).

    Examples:
        excel_serial_to_date(46094)  -> date(2026, 2, 28)
        excel_serial_to_date("")     -> None
        excel_serial_to_date(None)   -> None
    """
    if value is None:
        return None
    try:
        n = int(float(str(value).strip()))
        if n <= 0:
            return None
        return _EXCEL_EPOCH + timedelta(days=n)
    except (ValueError, TypeError, OverflowError):
        return None


def parse_decimal(value, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """Safely convert a CSV string value to Decimal. Returns default on failure."""
    if value is None:
        return default
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return default


def parse_int(value, default: Optional[int] = None) -> Optional[int]:
    """Safely convert a CSV string value to int. Returns default on failure."""
    if value is None:
        return default
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return default


def parse_bool_yn(value, default: bool = False) -> bool:
    """Convert Y/N string to bool. Case-insensitive. Returns default for anything else."""
    if value is None:
        return default
    return str(value).strip().upper() == "Y"


def parse_bool_truefalse(value, default: bool = False) -> bool:
    """Convert TRUE/FALSE string to bool. Case-insensitive."""
    if value is None:
        return default
    return str(value).strip().upper() == "TRUE"


def read_csv_rows(stream_or_path) -> Iterator[dict]:
    """
    Read a CSV file and yield one dict per row.

    Handles:
    - File-like objects (from Flask request.files) and file paths (strings)
    - UTF-8 BOM (common in Excel exports) via encoding='utf-8-sig'
    - Strips whitespace from all keys and values

    Usage:
        for row in read_csv_rows(request.files['file'].stream):
            ...
    """
    if isinstance(stream_or_path, (str, bytes)):
        # File path
        with open(stream_or_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield {k.strip(): (v.strip() if v else v) for k, v in row.items()}
    else:
        # File-like object — read bytes and decode
        raw = stream_or_path.read()
        if isinstance(raw, bytes):
            text = raw.decode("utf-8-sig")
        else:
            text = raw
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            yield {k.strip(): (v.strip() if v else v) for k, v in row.items()}
