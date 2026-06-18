"""Tests for delta detection and state management."""

import csv
import json
from pathlib import Path

from src.config import Config
from src.delta import (
    commit_state_entries,
    get_row_signature,
    identify_new_rows,
    load_state,
    write_staging_csv,
)
from src.delta import PendingStateEntry


def make_config(tmp_path: Path) -> Config:
    return Config(
        ms_tenant_id=None,
        ms_client_id=None,
        ms_client_secret=None,
        ms_drive_id=None,
        ms_drive_owner=None,
        ms_refresh_token=None,
        ms_token_cache_path="ms_graph_token_cache.json",
        ms_file_id=None,
        ms_file_path="Emergency_Alert_Registrations(in).csv",
        everbridge_transport="sftp",
        sftp_host="sftp.example.com",
        sftp_port=22,
        sftp_username="user",
        sftp_key_path=str(tmp_path / "key"),
        sftp_remote_dir="/update",
        sftp_remote_filename="upload.csv",
        sftp_remote_delete_dir="/delete",
        sftp_remote_delete_filename="upload.csv",
        delete_staging_csv="delete.csv",
        state_file=str(tmp_path / "sync_state.json"),
        local_master_copy=str(tmp_path / "master.csv"),
        upload_staging_csv=str(tmp_path / "staging.csv"),
        sent_files_dir=str(tmp_path / "sent"),
        failed_uploads_dir=str(tmp_path / "failed"),
        rejected_rows_csv=str(tmp_path / "rejected.csv"),
        local_fallback_csv=str(tmp_path / "fallback.csv"),
        allow_local_fallback=False,
        skip_graph_download=False,
        graph_max_retries=3,
        sftp_max_retries=3,
        sftp_timeout_seconds=60,
        sync_timezone="America/New_York",
        sync_day_of_week="fri",
        sync_hour=10,
        sync_minute=0,
        teams_webhook_url=None,
        teams_notify_on_success=False,
        smtp_host=None,
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        alert_email_to=None,
        alert_email_from=None,
    )


def write_master(path: Path, rows: list[dict[str, str]]) -> None:
    headers = list(rows[0].keys()) if rows else ["External ID", "First Name", "Last Name", "Phone 1"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_get_row_signature_is_stable():
    row = {"External ID": "1", "First Name": "Ada", "Last Name": "Lovelace"}
    assert get_row_signature(row) == get_row_signature(
        {"Last Name": "Lovelace", "External ID": "1", "First Name": "Ada"}
    )


def test_get_row_signature_handles_none_values_and_keys():
    row = {
        "External ID": "1",
        "First Name": None,
        None: "orphan column",
        "Phone 1": "",
    }
    signature = get_row_signature(row)
    assert len(signature) == 64
    assert signature == get_row_signature(
        {"External ID": "1", "First Name": "", "Phone 1": ""}
    )


def test_identify_new_rows_detects_new_and_updated(tmp_path):
    config = make_config(tmp_path)
    write_master(
        Path(config.local_master_copy),
        [
            {
                "External ID": "100",
                "First Name": "Ada",
                "Last Name": "Lovelace",
                "Phone 1": "9195550100",
            },
            {
                "External ID": "200",
                "First Name": "Grace",
                "Last Name": "Hopper",
                "Phone 1": "9195550200",
            },
        ],
    )

    first = identify_new_rows(config, "batch-1")
    assert len(first.new_rows) == 2
    assert first.new_count == 2
    assert first.update_count == 0

    commit_state_entries(config, first.pending_entries)

    write_master(
        Path(config.local_master_copy),
        [
            {
                "External ID": "100",
                "First Name": "Ada",
                "Last Name": "Lovelace",
                "Phone 1": "9195550100",
            },
            {
                "External ID": "200",
                "First Name": "Grace",
                "Last Name": "Hopper",
                "Phone 1": "9195559999",
            },
            {
                "External ID": "300",
                "First Name": "Katherine",
                "Last Name": "Johnson",
                "Phone 1": "9195550300",
            },
        ],
    )

    second = identify_new_rows(config, "batch-2")
    assert len(second.new_rows) == 2
    assert second.new_count == 1
    assert second.update_count == 1


def test_state_not_committed_until_explicit_call(tmp_path):
    config = make_config(tmp_path)
    write_master(
        Path(config.local_master_copy),
        [
            {
                "External ID": "1",
                "First Name": "Test",
                "Last Name": "User",
                "Phone 1": "9195550100",
            }
        ],
    )

    identify_new_rows(config, "batch-1")
    assert not Path(config.state_file).exists()


def test_commit_writes_lean_state_without_row_data(tmp_path):
    config = make_config(tmp_path)
    entry = PendingStateEntry(
        signature="abc123",
        external_id="1",
        processed_at="2026-01-01T00:00:00",
        upload_batch_id="batch-1",
        is_update=False,
    )
    commit_state_entries(config, [entry])

    state, _, _ = load_state(config)
    assert state[0]["external_id"] == "1"
    assert "row_data" not in state[0]


def test_write_staging_csv(tmp_path):
    config = make_config(tmp_path)
    rows = [
        {
            "External ID": "1",
            "First Name": "Test",
            "Last Name": "User",
            "Phone 1": "9195550100",
        }
    ]
    write_staging_csv(config, list(rows[0].keys()), rows)
    assert Path(config.upload_staging_csv).exists()


def test_write_staging_csv_ignores_blank_header_columns(tmp_path):
    config = make_config(tmp_path)
    headers = ["External ID", "Phone 1", None, ""]
    rows = [
        {
            "External ID": "1",
            "Phone 1": "9195550100",
            None: "orphan",
            "": "blank header",
        }
    ]
    write_staging_csv(config, headers, rows)
    content = Path(config.upload_staging_csv).read_text(encoding="utf-8")
    assert "orphan" not in content
    assert "External ID" in content
