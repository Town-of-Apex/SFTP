"""Delta detection and sync state management."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.config import Config
from src.csv_utils import normalize_csv_fieldnames, normalize_csv_row

logger = logging.getLogger("everbridge-sync.delta")


@dataclass
class PendingStateEntry:
    signature: str
    external_id: str
    processed_at: str
    upload_batch_id: str
    is_update: bool


@dataclass
class DeltaResult:
    new_rows: list[dict[str, str]]
    headers: list[str]
    pending_entries: list[PendingStateEntry]
    new_count: int
    update_count: int


def get_row_signature(row: dict[str, Any]) -> str:
    """Stable SHA256 of row content; tolerates None keys/values from CSV quirks."""
    normalized = {
        str(key): "" if value is None else str(value)
        for key, value in row.items()
        if key is not None
    }
    row_str = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(row_str.encode()).hexdigest()


def _external_id_from_row(row: dict[str, str]) -> str:
    return (row.get("External ID") or "").strip()


def load_state(config: Config) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    if not config.state_file:
        return [], set(), set()

    try:
        with open(config.state_file, encoding="utf-8") as handle:
            processed_state = json.load(handle)
    except FileNotFoundError:
        return [], set(), set()
    except json.JSONDecodeError:
        logger.warning("State file is corrupt; starting with empty state.")
        return [], set(), set()

    signatures = {entry["signature"] for entry in processed_state}
    external_ids = {
        entry.get("external_id", "")
        for entry in processed_state
        if entry.get("external_id")
    }
    return processed_state, signatures, external_ids


def identify_new_rows(config: Config, upload_batch_id: str) -> DeltaResult:
    if not config.local_master_copy:
        raise FileNotFoundError("Local master copy path is not configured.")

    processed_state, processed_signatures, known_external_ids = load_state(config)

    new_rows: list[dict[str, str]] = []
    pending_entries: list[PendingStateEntry] = []
    new_count = 0
    update_count = 0

    with open(config.local_master_copy, encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = normalize_csv_fieldnames(reader.fieldnames)

        for raw_row in reader:
            row = normalize_csv_row(raw_row, headers)
            signature = get_row_signature(row)
            if signature in processed_signatures:
                continue

            external_id = _external_id_from_row(row)
            is_update = bool(external_id and external_id in known_external_ids)
            if is_update:
                update_count += 1
            else:
                new_count += 1
                if external_id:
                    known_external_ids.add(external_id)

            new_rows.append(row)
            pending_entries.append(
                PendingStateEntry(
                    signature=signature,
                    external_id=external_id,
                    processed_at=datetime.now().isoformat(),
                    upload_batch_id=upload_batch_id,
                    is_update=is_update,
                )
            )
            processed_signatures.add(signature)

    logger.info(
        "Identified %s delta row(s): %s new, %s update(s).",
        len(new_rows),
        new_count,
        update_count,
    )
    return DeltaResult(
        new_rows=new_rows,
        headers=headers,
        pending_entries=pending_entries,
        new_count=new_count,
        update_count=update_count,
    )


def write_staging_csv(
    config: Config, headers: list[str], rows: list[dict[str, str]]
) -> None:
    clean_headers = normalize_csv_fieldnames(headers)
    clean_rows = [normalize_csv_row(row, clean_headers) for row in rows]
    with open(config.upload_staging_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=clean_headers,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(clean_rows)


def load_master_headers(config: Config) -> list[str]:
    """Read column headers from the cached master CSV."""
    with open(config.local_master_copy, encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = normalize_csv_fieldnames(reader.fieldnames)
    if not headers:
        raise ValueError(f"No headers found in master CSV: {config.local_master_copy}")
    return headers


def write_delete_staging_csv(
    config: Config,
    headers: list[str],
    external_id: str,
) -> None:
    """Write a sparse delete CSV with only External ID populated."""
    clean_headers = normalize_csv_fieldnames(headers)
    row = {header: "" for header in clean_headers}
    if "External ID" not in clean_headers:
        raise ValueError("Master CSV headers do not include 'External ID'.")
    row["External ID"] = external_id
    with open(config.delete_staging_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=clean_headers,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerow(row)


def commit_state_entries(
    config: Config,
    pending_entries: list[PendingStateEntry],
) -> None:
    processed_state, _, _ = load_state(config)

    for entry in pending_entries:
        processed_state.append(
            {
                "signature": entry.signature,
                "external_id": entry.external_id,
                "processed_at": entry.processed_at,
                "upload_batch_id": entry.upload_batch_id,
                "is_update": entry.is_update,
            }
        )

    with open(config.state_file, "w", encoding="utf-8") as handle:
        json.dump(processed_state, handle, indent=2)

    logger.info("Committed %s state entries after successful upload.", len(pending_entries))


def purge_external_id_from_state(config: Config, external_id: str) -> int:
    """Remove all sync state entries for an External ID.

    Used by admin reset today; will also be called after Everbridge contact
    deletion during HR offboarding. See docs/FUTURE_ARCHITECTURE.md.
    """
    state, _, _ = load_state(config)
    filtered = [entry for entry in state if entry.get("external_id") != external_id]
    removed = len(state) - len(filtered)

    with open(config.state_file, "w", encoding="utf-8") as handle:
        json.dump(filtered, handle, indent=2)

    logger.info(
        "Purged %s state entries for External ID '%s'.",
        removed,
        external_id,
    )
    return removed


def count_staged_rows(config: Config) -> int:
    try:
        with open(config.upload_staging_csv, encoding="utf-8") as handle:
            return max(0, sum(1 for _ in csv.reader(handle)) - 1)
    except FileNotFoundError:
        return 0
