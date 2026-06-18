"""CSV row validation before Everbridge upload."""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass

from src.config import Config
from src.csv_utils import normalize_csv_fieldnames, normalize_csv_row

logger = logging.getLogger("everbridge-sync.validation")

PHONE_PATTERN = re.compile(r"^[\d\s()+\-.]{7,}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class ValidationIssue:
    external_id: str
    reason: str


def _has_contact_method(row: dict[str, str]) -> bool:
    phone = (row.get("Phone 1") or "").strip()
    email = (row.get("Email Address 1") or "").strip()
    if phone and PHONE_PATTERN.match(phone):
        return True
    if email and EMAIL_PATTERN.match(email):
        return True
    return False


def validate_row(row: dict[str, str]) -> list[str]:
    issues: list[str] = []
    external_id = (row.get("External ID") or "").strip()
    first_name = (row.get("First Name") or "").strip()
    last_name = (row.get("Last Name") or "").strip()

    if not external_id:
        issues.append("Missing External ID")
    if not first_name:
        issues.append("Missing First Name")
    if not last_name:
        issues.append("Missing Last Name")
    if not _has_contact_method(row):
        issues.append("Missing valid Phone 1 or Email Address 1")

    phone = (row.get("Phone 1") or "").strip()
    if phone and not PHONE_PATTERN.match(phone):
        issues.append("Phone 1 has invalid format")

    email = (row.get("Email Address 1") or "").strip()
    if email and not EMAIL_PATTERN.match(email):
        issues.append("Email Address 1 has invalid format")

    return issues


def partition_rows(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[ValidationIssue]]:
    valid_rows: list[dict[str, str]] = []
    invalid_rows: list[ValidationIssue] = []

    for row in rows:
        issues = validate_row(row)
        if issues:
            invalid_rows.append(
                ValidationIssue(
                    external_id=(row.get("External ID") or "").strip(),
                    reason="; ".join(issues),
                )
            )
        else:
            valid_rows.append(row)

    return valid_rows, invalid_rows


def write_rejected_rows(
    config: Config,
    headers: list[str],
    rows: list[dict[str, str]],
    issues: list[ValidationIssue],
) -> None:
    if not rows:
        return

    fieldnames = normalize_csv_fieldnames(headers) + ["rejection_reason"]
    with open(config.rejected_rows_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row, issue in zip(rows, issues, strict=True):
            enriched = normalize_csv_row(row, fieldnames[:-1])
            enriched["rejection_reason"] = issue.reason
            writer.writerow(enriched)

    logger.warning(
        "Wrote %s rejected row(s) to %s.",
        len(rows),
        config.rejected_rows_csv,
    )
