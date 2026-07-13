"""Shared Everbridge contact delete logic for CLI and HTTP API."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime

from src.config import Config
from src.contacts import contact_name_for_external_id
from src.delta import (
    load_master_headers,
    purge_external_id_from_state,
    write_delete_staging_csv,
)
from src.everbridge import EverbridgeTransportError, create_transport
from src.graph_client import download_delegated_master
from src.notifications import send_delete_alert, send_failure_alert

logger = logging.getLogger("everbridge-sync.delete")


class DeleteError(Exception):
    """Base class for delete failures."""


class MasterUnavailableError(DeleteError):
    """Master CSV is missing and could not be downloaded."""


class DeleteTransportError(DeleteError):
    """Everbridge transport failed during delete."""


@dataclass(frozen=True)
class DeleteResult:
    employee_id: str
    contact_name: str
    archive_path: str | None
    state_entries_removed: int
    dry_run: bool = False


def ensure_master_for_delete(config: Config) -> None:
    """Ensure local master CSV exists (for headers / name lookup)."""
    if os.path.exists(config.local_master_copy):
        return

    if config.skip_graph_download:
        raise MasterUnavailableError(
            f"Master CSV not found at {config.local_master_copy}. "
            "Run a sync first or disable SKIP_GRAPH_DOWNLOAD to download from OneDrive."
        )

    if not download_delegated_master(config):
        raise MasterUnavailableError(
            f"Could not download master CSV to {config.local_master_copy}. "
            "Run a sync first or check Graph credentials."
        )


def archive_delete_csv(config: Config, external_id: str) -> str:
    os.makedirs(config.sent_files_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f"delete_{timestamp}_{external_id}.csv"
    archive_path = os.path.join(config.sent_files_dir, archive_name)
    shutil.copy(config.delete_staging_csv, archive_path)
    return archive_path


def delete_external_id(
    config: Config,
    external_id: str,
    *,
    dry_run: bool = False,
    notify: bool = True,
) -> DeleteResult:
    """
    Delete one Everbridge contact by External ID (employee ID).

    Uploads a sparse delete CSV via the configured transport, archives it,
    purges local sync state, and optionally sends a Teams alert.
    """
    employee_id = external_id.strip()
    if not employee_id:
        raise ValueError("employeeId is required.")

    ensure_master_for_delete(config)
    contact_name = contact_name_for_external_id(config, employee_id)

    if dry_run:
        headers = load_master_headers(config)
        write_delete_staging_csv(config, headers, employee_id)
        logger.info(
            "Dry run: delete CSV written for %s (%s) → %s",
            contact_name,
            employee_id,
            config.delete_staging_csv,
        )
        return DeleteResult(
            employee_id=employee_id,
            contact_name=contact_name,
            archive_path=None,
            state_entries_removed=0,
            dry_run=True,
        )

    try:
        create_transport(config).delete_contact(employee_id)
        archive_path = archive_delete_csv(config, employee_id)
        removed = purge_external_id_from_state(config, employee_id)
    except EverbridgeTransportError as exc:
        if notify:
            send_failure_alert(
                config,
                "sftp",
                str(exc),
                {
                    "operation": "delete",
                    "contact": contact_name,
                },
            )
        raise DeleteTransportError(str(exc)) from exc

    logger.info(
        "Deleted contact '%s' (External ID %s). Removed %s local state entries.",
        contact_name,
        employee_id,
        removed,
    )

    if notify:
        send_delete_alert(
            config,
            "success",
            {
                "contact": contact_name,
                "archive": archive_path,
            },
        )

    return DeleteResult(
        employee_id=employee_id,
        contact_name=contact_name,
        archive_path=archive_path,
        state_entries_removed=removed,
        dry_run=False,
    )
