"""Administrative CLI for sync state and operations."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime

from src.config import load_config
from src.contacts import contact_name_for_external_id, format_contact_name
from src.delta import (
    identify_new_rows,
    load_master_headers,
    load_state,
    purge_external_id_from_state,
    write_delete_staging_csv,
)
from src.everbridge import EverbridgeTransportError, create_transport
from src.graph_client import download_delegated_master
from src.logging_config import setup_logging
from src.notifications import send_delete_alert, send_failure_alert
from src.pipeline import run_sync


def cmd_status(config) -> None:
    state, signatures, external_ids = load_state(config)
    pending = 0
    if os.path.exists(config.local_master_copy):
        delta = identify_new_rows(config, "preview")
        pending = len(delta.new_rows)

    print(f"State file: {config.state_file}")
    print(f"Processed signatures: {len(signatures)}")
    print(f"Known external IDs: {len(external_ids)}")
    print(f"Pending delta rows: {pending}")
    if state:
        last = max(state, key=lambda entry: entry.get("processed_at", ""))
        print(f"Last processed at: {last.get('processed_at', 'unknown')}")


def cmd_preview(config) -> None:
    if not os.path.exists(config.local_master_copy):
        print(f"Master file not found: {config.local_master_copy}")
        sys.exit(1)

    delta = identify_new_rows(config, "preview")
    print(
        f"Would upload {len(delta.new_rows)} row(s): "
        f"{delta.new_count} new, {delta.update_count} update(s)."
    )
    for row in delta.new_rows[:20]:
        print(f"  - {format_contact_name(row)}")
    if len(delta.new_rows) > 20:
        print(f"  ... and {len(delta.new_rows) - 20} more")


def cmd_replay(config, archive_path: str) -> None:
    if not os.path.exists(archive_path):
        print(f"Archive not found: {archive_path}")
        sys.exit(1)

    import shutil

    shutil.copy(archive_path, config.upload_staging_csv)
    create_transport(config).upsert_batch(config.upload_staging_csv)
    print(f"Replayed upload from {archive_path}")


def cmd_reset_external_id(config, external_id: str) -> None:
    removed = purge_external_id_from_state(config, external_id)
    print(
        f"Removed {removed} state entries for External ID '{external_id}'. "
        "Next sync will re-upload matching rows from the master CSV if present."
    )


def _ensure_master_for_delete(config) -> None:
    if os.path.exists(config.local_master_copy):
        return

    if config.skip_graph_download:
        print(
            f"Master CSV not found at {config.local_master_copy}. "
            "Run a sync first or disable SKIP_GRAPH_DOWNLOAD to download from OneDrive."
        )
        sys.exit(1)

    if not download_delegated_master(config):
        print(
            f"Could not download master CSV to {config.local_master_copy}. "
            "Run a sync first or check Graph credentials."
        )
        sys.exit(1)


def _archive_delete_csv(config, external_id: str) -> str:
    os.makedirs(config.sent_files_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f"delete_{timestamp}_{external_id}.csv"
    archive_path = os.path.join(config.sent_files_dir, archive_name)
    shutil.copy(config.delete_staging_csv, archive_path)
    return archive_path


def cmd_delete_external_id(
    config,
    external_id: str,
    *,
    assume_yes: bool = False,
    dry_run: bool = False,
) -> None:
    external_id = external_id.strip()
    if not external_id:
        print("External ID is required.")
        sys.exit(1)

    _ensure_master_for_delete(config)
    contact_name = contact_name_for_external_id(config, external_id)
    remote_path = (
        f"{config.sftp_remote_delete_dir}/{config.sftp_remote_delete_filename}"
    )

    print(f"Contact: {contact_name}")
    print(f"External ID: {external_id}")
    print(f"SFTP target: {config.sftp_host}{remote_path}")

    if dry_run:
        headers = load_master_headers(config)
        write_delete_staging_csv(config, headers, external_id)
        print(f"Dry run: delete CSV written to {config.delete_staging_csv}")
        print("No SFTP upload performed.")
        return

    if not assume_yes:
        print(
            "\nAbout to DELETE this contact from Everbridge.\n"
            "Deleted contacts can be restored for 30 days following today's deletion.\n"
        )
        confirmation = input(
            f"Type the External ID again to confirm deletion: "
        ).strip()
        if confirmation != external_id:
            print("Confirmation did not match. Delete cancelled.")
            sys.exit(1)

    try:
        create_transport(config).delete_contact(external_id)
        archive_path = _archive_delete_csv(config, external_id)
        removed = purge_external_id_from_state(config, external_id)
        print(
            f"Deleted contact '{contact_name}' (External ID {external_id}). "
            f"Removed {removed} local state entries."
        )
        print(f"Archived delete CSV to {archive_path}")
        send_delete_alert(
            config,
            "success",
            {
                "contact": contact_name,
                "archive": archive_path,
            },
        )
    except EverbridgeTransportError as exc:
        send_failure_alert(
            config,
            "sftp",
            str(exc),
            {
                "operation": "delete",
                "contact": contact_name,
            },
        )
        print(f"Delete failed: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    setup_logging()
    config = load_config()
    parser = argparse.ArgumentParser(description="Everbridge sync admin tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show sync state summary")
    subparsers.add_parser("preview", help="Preview rows that would upload")
    subparsers.add_parser("run", help="Run sync immediately")

    replay_parser = subparsers.add_parser("replay", help="Re-upload an archived CSV")
    replay_parser.add_argument("archive_path", help="Path to archived CSV in sent_files/")

    reset_parser = subparsers.add_parser(
        "reset-external-id",
        help="Remove local sync state for one External ID (re-upload on next sync; not an Everbridge delete)",
    )
    reset_parser.add_argument("external_id")

    delete_parser = subparsers.add_parser(
        "delete-external-id",
        help="Delete a contact from Everbridge via SFTP and purge local sync state",
    )
    delete_parser.add_argument("external_id")
    delete_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip interactive confirmation",
    )
    delete_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the delete CSV only; do not upload to Everbridge",
    )

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(config)
    elif args.command == "preview":
        cmd_preview(config)
    elif args.command == "run":
        result = run_sync(config)
        print(
            f"Sync {result.status}: uploaded={result.rows_uploaded}, "
            f"rejected={result.rows_rejected}"
        )
    elif args.command == "replay":
        cmd_replay(config, args.archive_path)
    elif args.command == "reset-external-id":
        cmd_reset_external_id(config, args.external_id)
    elif args.command == "delete-external-id":
        cmd_delete_external_id(
            config,
            args.external_id,
            assume_yes=args.yes,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
