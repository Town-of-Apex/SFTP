"""Master CSV analytics metadata (never sent to Everbridge)."""

from __future__ import annotations

# Appended after the Everbridge template `END` column by Power Automate.
METADATA_COLUMNS = (
    "Opted In",
    "Submitter Email",
    "Submitter Department",
    "Submission Datetime",
)

_TRUE_VALUES = frozenset({"TRUE", "1", "YES"})
_FALSE_VALUES = frozenset({"FALSE", "0", "NO"})


def has_opted_in_column(headers: list[str]) -> bool:
    return "Opted In" in headers


def parse_opted_in(row: dict[str, str], *, column_present: bool) -> bool | None:
    """Return True/False for opt preference, or None if invalid.

    Legacy masters without an `Opted In` column are treated as all opted in.
    """
    if not column_present:
        return True

    raw = (row.get("Opted In") or "").strip().upper()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return None


def everbridge_headers(headers: list[str]) -> list[str]:
    """Headers safe to send to Everbridge: through END, excluding metadata."""
    result: list[str] = []
    for header in headers:
        if header in METADATA_COLUMNS:
            continue
        result.append(header)
        if header == "END":
            break
    return result


def strip_metadata(row: dict[str, str]) -> dict[str, str]:
    """Return a shallow copy of row without analytics metadata columns."""
    return {key: value for key, value in row.items() if key not in METADATA_COLUMNS}
