"""Delta detection and sync state management."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.config import Config
from src.csv_utils import normalize_csv_fieldnames, normalize_csv_row
from src.metadata import everbridge_headers, has_opted_in_column, parse_opted_in

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
    headers: list[str]
    opt_in_rows: list[dict[str, str]]
    opt_out_rows: list[dict[str, str]]
    invalid_preference_rows: list[dict[str, str]]
    pending_entries: list[PendingStateEntry]
    new_count: int
    update_count: int
    delete_count: int

    @property
    def actionable_count(self) -> int:
        return (
            len(self.opt_in_rows)
            + len(self.opt_out_rows)
            + len(self.invalid_preference_rows)
        )


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
    """Detect actionable deltas using last-write-wins per External ID.

    For each External ID, only the latest master row is considered for UPDATE or
    DELETE. After a successful sync, callers commit pending entries that seal
    *all* current master signatures for each processed External ID so older
    opt-in rows cannot resurrect a contact after an opt-out.
    """
    if not config.local_master_copy:
        raise FileNotFoundError("Local master copy path is not configured.")

    _, processed_signatures, known_external_ids = load_state(config)

    with open(config.local_master_copy, encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = normalize_csv_fieldnames(reader.fieldnames)
        rows = [normalize_csv_row(raw_row, headers) for raw_row in reader]

    column_present = has_opted_in_column(headers)
    processed_at = datetime.now().isoformat()

    signatures_by_id: dict[str, list[str]] = defaultdict(list)
    last_index_by_id: dict[str, int] = {}
    row_signatures: list[str] = []

    for index, row in enumerate(rows):
        signature = get_row_signature(row)
        row_signatures.append(signature)
        external_id = _external_id_from_row(row)
        if external_id:
            signatures_by_id[external_id].append(signature)
            last_index_by_id[external_id] = index

    opt_in_rows: list[dict[str, str]] = []
    opt_out_rows: list[dict[str, str]] = []
    invalid_preference_rows: list[dict[str, str]] = []
    pending_entries: list[PendingStateEntry] = []
    pending_signatures: set[str] = set()
    new_count = 0
    update_count = 0
    delete_count = 0

    def _queue_seal_entries(external_id: str, *, is_update: bool) -> None:
        for signature in signatures_by_id[external_id]:
            if signature in processed_signatures or signature in pending_signatures:
                continue
            pending_entries.append(
                PendingStateEntry(
                    signature=signature,
                    external_id=external_id,
                    processed_at=processed_at,
                    upload_batch_id=upload_batch_id,
                    is_update=is_update,
                )
            )
            pending_signatures.add(signature)

    for index, row in enumerate(rows):
        signature = row_signatures[index]
        external_id = _external_id_from_row(row)

        if external_id:
            if last_index_by_id[external_id] != index:
                continue
            if signature in processed_signatures:
                continue
        else:
            if signature in processed_signatures:
                continue

        opted_in = parse_opted_in(row, column_present=column_present)
        if opted_in is None:
            invalid_preference_rows.append(row)
            continue

        if external_id:
            is_update = external_id in known_external_ids
            if opted_in:
                opt_in_rows.append(row)
                if is_update:
                    update_count += 1
                else:
                    new_count += 1
                    known_external_ids.add(external_id)
            else:
                opt_out_rows.append(row)
                delete_count += 1
            _queue_seal_entries(external_id, is_update=is_update and opted_in)
        else:
            # Blank External ID: surface for validation rejection; no seal group.
            if opted_in:
                opt_in_rows.append(row)
                new_count += 1
            else:
                opt_out_rows.append(row)
                delete_count += 1
            if signature not in pending_signatures:
                pending_entries.append(
                    PendingStateEntry(
                        signature=signature,
                        external_id="",
                        processed_at=processed_at,
                        upload_batch_id=upload_batch_id,
                        is_update=False,
                    )
                )
                pending_signatures.add(signature)

    logger.info(
        "Identified %s actionable row(s): %s new, %s update(s), %s delete(s), "
        "%s invalid Opted In.",
        len(opt_in_rows) + len(opt_out_rows) + len(invalid_preference_rows),
        new_count,
        update_count,
        delete_count,
        len(invalid_preference_rows),
    )
    return DeltaResult(
        headers=headers,
        opt_in_rows=opt_in_rows,
        opt_out_rows=opt_out_rows,
        invalid_preference_rows=invalid_preference_rows,
        pending_entries=pending_entries,
        new_count=new_count,
        update_count=update_count,
        delete_count=delete_count,
    )


def write_staging_csv(
    config: Config, headers: list[str], rows: list[dict[str, str]]
) -> None:
    clean_headers = everbridge_headers(normalize_csv_fieldnames(headers))
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
    external_ids: str | list[str],
) -> None:
    """Write a sparse delete CSV with only External ID populated (one or more rows)."""
    if isinstance(external_ids, str):
        ids = [external_ids]
    else:
        ids = list(external_ids)

    clean_headers = everbridge_headers(normalize_csv_fieldnames(headers))
    if "External ID" not in clean_headers:
        raise ValueError("Master CSV headers do not include 'External ID'.")

    with open(config.delete_staging_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=clean_headers,
            extrasaction="ignore",
        )
        writer.writeheader()
        for external_id in ids:
            row = {header: "" for header in clean_headers}
            row["External ID"] = external_id
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

    Used by admin reset and on-demand HR delete. Form opt-outs in the scheduled
    sync seal signatures instead of purging, so stale opt-in rows cannot re-upload.
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
