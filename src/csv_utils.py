"""CSV normalization for sparse Everbridge template exports."""

from __future__ import annotations

from typing import Any


def normalize_csv_fieldnames(fieldnames: list[str] | None) -> list[str]:
    """Drop blank/None headers from DictReader fieldnames."""
    if not fieldnames:
        return []
    headers: list[str] = []
    for name in fieldnames:
        if name is None:
            continue
        key = str(name)
        if not key.strip():
            continue
        headers.append(key)
    return headers


def normalize_csv_row(row: dict[str, Any], headers: list[str]) -> dict[str, str]:
    """Map a DictReader row to string values for known headers only."""
    return {
        header: "" if row.get(header) is None else str(row.get(header, ""))
        for header in headers
    }
