"""Sync pipeline orchestration."""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.config import Config, load_config
from src.contacts import (
    build_sync_failure_contact_context,
    build_sync_success_contact_context,
)
from src.delta import (
    commit_state_entries,
    identify_new_rows,
    write_delete_staging_csv,
    write_staging_csv,
)
from src.everbridge import EverbridgeTransportError, create_transport
from src.graph_client import download_delegated_master
from src.notifications import send_failure_alert, send_success_alert
from src.validation import (
    ValidationIssue,
    partition_rows,
    validate_opt_out_row,
    validate_row,
    write_rejected_rows,
)

logger = logging.getLogger("everbridge-sync.pipeline")

FailureType = Literal["auth", "download", "filter", "validation", "sftp", "unknown"]


@dataclass
class SyncResult:
    sync_run_id: str
    rows_uploaded: int
    rows_deleted: int
    rows_rejected: int
    status: Literal["success", "no_action", "failed"]


class SyncPipelineError(Exception):
    def __init__(self, failure_type: FailureType, message: str):
        super().__init__(message)
        self.failure_type = failure_type
        self.message = message


def _ensure_directories(config: Config) -> None:
    os.makedirs(config.sent_files_dir, exist_ok=True)
    os.makedirs(config.failed_uploads_dir, exist_ok=True)


def _use_local_master(config: Config, *, allow_existing_master: bool) -> None:
    if os.path.exists(config.local_fallback_csv):
        logger.warning(
            "Using local source file '%s' instead of OneDrive.",
            config.local_fallback_csv,
        )
        shutil.copy(config.local_fallback_csv, config.local_master_copy)
        return

    if allow_existing_master and os.path.exists(config.local_master_copy):
        logger.warning(
            "Using existing local master file '%s' instead of OneDrive.",
            config.local_master_copy,
        )
        return

    locations = f"'{config.local_fallback_csv}'"
    if allow_existing_master:
        locations += f" or '{config.local_master_copy}'"

    raise SyncPipelineError(
        "download",
        f"No local CSV found at {locations}.",
    )


def _download_master(config: Config) -> None:
    if config.skip_graph_download:
        _use_local_master(config, allow_existing_master=True)
        return

    if download_delegated_master(config):
        return

    if config.allow_local_fallback:
        _use_local_master(config, allow_existing_master=False)
        return

    if not config.graph_delegated_configured:
        raise SyncPipelineError(
            "auth",
            "Microsoft Graph delegated auth is not configured. Set MS_TENANT_ID, "
            "MS_CLIENT_ID, MS_CLIENT_SECRET, run explore_onedrive.py --device-login "
            "on the host to create the token cache, or enable SKIP_GRAPH_DOWNLOAD=true "
            "for local CSV testing.",
        )

    raise SyncPipelineError(
        "download",
        "Failed to download master CSV from OneDrive and local fallback is disabled.",
    )


def _archive_upload(config: Config, sync_run_id: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f"upload_{timestamp}_{sync_run_id[:8]}.csv"
    archive_path = os.path.join(config.sent_files_dir, archive_name)
    shutil.move(config.upload_staging_csv, archive_path)
    logger.info("Archived upload to: %s", archive_path)
    return archive_path


def _archive_delete(config: Config, sync_run_id: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f"delete_{timestamp}_{sync_run_id[:8]}.csv"
    archive_path = os.path.join(config.sent_files_dir, archive_name)
    shutil.move(config.delete_staging_csv, archive_path)
    logger.info("Archived delete to: %s", archive_path)
    return archive_path


def _archive_failure(
    config: Config,
    sync_run_id: str,
    failure_type: FailureType,
    message: str,
    row_count: int,
) -> str | None:
    staging_candidates = [
        config.upload_staging_csv,
        config.delete_staging_csv,
    ]
    staging_path = next(
        (path for path in staging_candidates if os.path.exists(path)),
        None,
    )
    if staging_path is None:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = f"failed_{timestamp}_{sync_run_id[:8]}"
    csv_path = os.path.join(config.failed_uploads_dir, f"{base_name}.csv")
    meta_path = os.path.join(config.failed_uploads_dir, f"{base_name}.json")

    shutil.copy(staging_path, csv_path)
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "sync_run_id": sync_run_id,
                "failure_type": failure_type,
                "message": message,
                "row_count": row_count,
                "timestamp": datetime.now().isoformat(),
                "staging_file": csv_path,
            },
            handle,
            indent=2,
        )

    logger.error("Preserved failed upload at %s", csv_path)
    return csv_path


def _handle_failure(
    config: Config,
    sync_run_id: str,
    failure_type: FailureType,
    message: str,
    row_count: int,
    rejected_count: int,
    *,
    valid_rows: list[dict[str, str]] | None = None,
    deleted_rows: list[dict[str, str]] | None = None,
    rejected_rows: list[dict[str, str]] | None = None,
    rejection_issues: list[ValidationIssue] | None = None,
) -> SyncResult:
    failed_path = _archive_failure(config, sync_run_id, failure_type, message, row_count)
    alert_context: dict[str, str | int] = {
        "sync_run_id": sync_run_id,
        "row_count": row_count,
        "failed_staging_file": failed_path or "n/a",
    }
    alert_context.update(
        build_sync_failure_contact_context(
            valid_rows or [],
            rejected_rows or [],
            rejection_issues or [],
            deleted_rows=deleted_rows,
        )
    )
    send_failure_alert(
        config,
        failure_type,
        message,
        alert_context,
    )
    logger.error("--- SYNC FAILED [%s]: %s ---", sync_run_id, message)
    return SyncResult(
        sync_run_id=sync_run_id,
        rows_uploaded=0,
        rows_deleted=0,
        rows_rejected=rejected_count,
        status="failed",
    )


def _filter_pending_for_valid(
    pending_entries,
    *,
    valid_opt_ins: list[dict[str, str]],
    valid_opt_outs: list[dict[str, str]],
):
    valid_external_ids = {
        (row.get("External ID") or "").strip()
        for row in valid_opt_ins + valid_opt_outs
        if (row.get("External ID") or "").strip()
    }
    # Blank-ID rows are never committed on success (they fail validation).
    return [
        entry
        for entry in pending_entries
        if entry.external_id and entry.external_id in valid_external_ids
    ]


def run_sync(config: Config | None = None) -> SyncResult:
    config = config or load_config()
    sync_run_id = str(uuid.uuid4())
    _ensure_directories(config)

    logger.info("--- SYNC START [%s] ---", sync_run_id)
    upload_count = 0
    delete_count = 0
    rejected_count = 0
    valid_opt_ins: list[dict[str, str]] = []
    valid_opt_outs: list[dict[str, str]] = []
    rejected_rows: list[dict[str, str]] = []
    rejection_issues: list[ValidationIssue] = []

    try:
        _download_master(config)

        delta = identify_new_rows(config, sync_run_id)
        if delta.actionable_count == 0:
            logger.info("Sync complete: no new opt-ins or opt-outs.")
            result = SyncResult(
                sync_run_id=sync_run_id,
                rows_uploaded=0,
                rows_deleted=0,
                rows_rejected=0,
                status="no_action",
            )
            send_success_alert(
                config,
                "no_action",
                {
                    "sync_run_id": sync_run_id,
                    "message": "No new opt-ins or opt-outs.",
                },
            )
            return result

        valid_opt_ins, opt_in_issues = partition_rows(
            delta.opt_in_rows, validator=validate_row
        )
        valid_opt_outs, opt_out_issues = partition_rows(
            delta.opt_out_rows, validator=validate_opt_out_row
        )

        valid_opt_in_ids = {id(row) for row in valid_opt_ins}
        valid_opt_out_ids = {id(row) for row in valid_opt_outs}
        rejected_rows = [
            row for row in delta.opt_in_rows if id(row) not in valid_opt_in_ids
        ] + [
            row for row in delta.opt_out_rows if id(row) not in valid_opt_out_ids
        ] + list(delta.invalid_preference_rows)

        rejection_issues = list(opt_in_issues) + list(opt_out_issues)
        for row in delta.invalid_preference_rows:
            rejection_issues.append(
                ValidationIssue(
                    external_id=(row.get("External ID") or "").strip(),
                    reason="Opted In must be TRUE or FALSE",
                )
            )

        if rejected_rows:
            write_rejected_rows(config, delta.headers, rejected_rows, rejection_issues)
            rejected_count = len(rejected_rows)

        if not valid_opt_ins and not valid_opt_outs:
            return _handle_failure(
                config,
                sync_run_id,
                "validation",
                f"All {rejected_count} delta row(s) failed validation.",
                0,
                rejected_count,
                rejected_rows=rejected_rows,
                rejection_issues=rejection_issues,
            )

        pending_entries = _filter_pending_for_valid(
            delta.pending_entries,
            valid_opt_ins=valid_opt_ins,
            valid_opt_outs=valid_opt_outs,
        )

        transport = create_transport(config)

        if valid_opt_ins:
            write_staging_csv(config, delta.headers, valid_opt_ins)
            upload_count = len(valid_opt_ins)
            transport.upsert_batch(config.upload_staging_csv)

        if valid_opt_outs:
            delete_ids = [
                (row.get("External ID") or "").strip() for row in valid_opt_outs
            ]
            write_delete_staging_csv(config, delta.headers, delete_ids)
            delete_count = len(valid_opt_outs)
            transport.delete_batch(config.delete_staging_csv)

        commit_state_entries(config, pending_entries)

        if valid_opt_ins and os.path.exists(config.upload_staging_csv):
            _archive_upload(config, sync_run_id)
        if valid_opt_outs and os.path.exists(config.delete_staging_csv):
            _archive_delete(config, sync_run_id)

        logger.info(
            "--- SYNC END [%s]: uploaded %s, deleted %s, rejected %s ---",
            sync_run_id,
            upload_count,
            delete_count,
            rejected_count,
        )
        result = SyncResult(
            sync_run_id=sync_run_id,
            rows_uploaded=upload_count,
            rows_deleted=delete_count,
            rows_rejected=rejected_count,
            status="success",
        )
        success_context: dict[str, str | int] = {
            "sync_run_id": sync_run_id,
            "rows_uploaded": upload_count,
            "rows_deleted": delete_count,
            "rows_rejected": rejected_count,
        }
        success_context.update(
            build_sync_success_contact_context(
                valid_opt_ins,
                rejected_rows,
                rejection_issues,
                deleted_rows=valid_opt_outs,
            )
        )
        send_success_alert(
            config,
            "success",
            success_context,
        )
        return result

    except SyncPipelineError as exc:
        return _handle_failure(
            config,
            sync_run_id,
            exc.failure_type,
            exc.message,
            upload_count + delete_count,
            rejected_count,
            valid_rows=valid_opt_ins,
            deleted_rows=valid_opt_outs,
            rejected_rows=rejected_rows,
            rejection_issues=rejection_issues,
        )

    except EverbridgeTransportError as exc:
        return _handle_failure(
            config,
            sync_run_id,
            "sftp",
            str(exc),
            upload_count + delete_count,
            rejected_count,
            valid_rows=valid_opt_ins,
            deleted_rows=valid_opt_outs,
            rejected_rows=rejected_rows,
            rejection_issues=rejection_issues,
        )

    except Exception as exc:
        failure_context: dict[str, str | int] = {
            "sync_run_id": sync_run_id,
            "row_count": upload_count + delete_count,
        }
        failure_context.update(
            build_sync_failure_contact_context(
                valid_opt_ins,
                rejected_rows,
                rejection_issues,
                deleted_rows=valid_opt_outs,
            )
        )
        send_failure_alert(
            config,
            "unknown",
            str(exc),
            failure_context,
        )
        logger.exception("--- SYNC FAILED [%s] ---", sync_run_id)
        raise
