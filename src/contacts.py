"""Contact name formatting for alerts and admin output."""

from __future__ import annotations

import csv
import logging

from src.config import Config
from src.csv_utils import normalize_csv_fieldnames, normalize_csv_row
from src.validation import ValidationIssue

logger = logging.getLogger("everbridge-sync.contacts")

DEFAULT_CONTACT_LIST_LIMIT = 20


def format_contact_name(row: dict[str, str]) -> str:
    name = (
        f"{row.get('First Name', '').strip()} {row.get('Last Name', '').strip()}".strip()
    )
    return name or "Unknown"


def format_contact_list(
    rows: list[dict[str, str]],
    *,
    issues_by_external_id: dict[str, str] | None = None,
    limit: int = DEFAULT_CONTACT_LIST_LIMIT,
) -> str:
    if not rows:
        return "none"

    labels: list[str] = []
    for row in rows:
        name = format_contact_name(row)
        external_id = (row.get("External ID") or "").strip()
        if issues_by_external_id and external_id in issues_by_external_id:
            labels.append(f"{name} ({issues_by_external_id[external_id]})")
        else:
            labels.append(name)

    if len(labels) > limit:
        shown = labels[:limit]
        return ", ".join(shown) + f"; ... and {len(labels) - limit} more"
    return ", ".join(labels)


def issues_by_external_id(issues: list[ValidationIssue]) -> dict[str, str]:
    return {
        issue.external_id: issue.reason
        for issue in issues
        if issue.external_id
    }


def build_sync_success_contact_context(
    valid_rows: list[dict[str, str]],
    rejected_rows: list[dict[str, str]],
    rejection_issues: list[ValidationIssue],
) -> dict[str, str]:
    context: dict[str, str] = {}
    if valid_rows:
        context["Succeeded contacts"] = format_contact_list(valid_rows)
    if rejected_rows:
        context["Failed contacts"] = format_contact_list(
            rejected_rows,
            issues_by_external_id=issues_by_external_id(rejection_issues),
        )
    return context


def build_sync_failure_contact_context(
    valid_rows: list[dict[str, str]],
    rejected_rows: list[dict[str, str]],
    rejection_issues: list[ValidationIssue],
) -> dict[str, str]:
    context: dict[str, str] = {}
    if valid_rows:
        context["Attempted contacts"] = format_contact_list(valid_rows)
    if rejected_rows:
        context["Failed contacts"] = format_contact_list(
            rejected_rows,
            issues_by_external_id=issues_by_external_id(rejection_issues),
        )
    return context


def find_contact_in_master(config: Config, external_id: str) -> dict[str, str] | None:
    """Return the master CSV row for an External ID, if present."""
    if not config.local_master_copy:
        return None

    try:
        with open(config.local_master_copy, encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headers = normalize_csv_fieldnames(reader.fieldnames)
            for raw_row in reader:
                row = normalize_csv_row(raw_row, headers)
                if (row.get("External ID") or "").strip() == external_id:
                    return row
    except FileNotFoundError:
        logger.debug("Master CSV not found for contact lookup: %s", config.local_master_copy)
    return None


def contact_name_for_external_id(config: Config, external_id: str) -> str:
    row = find_contact_in_master(config, external_id)
    if row:
        return format_contact_name(row)
    return "Unknown"
