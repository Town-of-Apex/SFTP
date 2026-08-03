"""Tests for sync pipeline orchestration."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import Config
from src.everbridge import EverbridgeTransportError
from src.pipeline import _download_master, run_sync


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
        delete_staging_csv=str(tmp_path / "delete.csv"),
        state_file=str(tmp_path / "sync_state.json"),
        local_master_copy=str(tmp_path / "master.csv"),
        upload_staging_csv=str(tmp_path / "staging.csv"),
        sent_files_dir=str(tmp_path / "sent"),
        failed_uploads_dir=str(tmp_path / "failed"),
        rejected_rows_csv=str(tmp_path / "rejected.csv"),
        local_fallback_csv=str(tmp_path / "fallback.csv"),
        allow_local_fallback=True,
        skip_graph_download=False,
        graph_max_retries=1,
        sftp_max_retries=1,
        sftp_timeout_seconds=5,
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


def test_run_sync_commits_state_only_after_upload(tmp_path):
    import csv

    config = make_config(tmp_path)
    fallback = Path(config.local_fallback_csv)
    master = Path(config.local_master_copy)

    rows = [
        {
            "External ID": "1",
            "First Name": "Test",
            "Last Name": "User",
            "Phone 1": "9195550100",
        }
    ]
    headers = list(rows[0].keys())
    for path in (fallback, master):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    mock_transport = MagicMock()
    mock_transport.return_value.upsert_batch.side_effect = EverbridgeTransportError("sftp down")

    with (
        patch("src.pipeline._download_master"),
        patch("src.pipeline.create_transport", mock_transport),
        patch("src.pipeline.send_failure_alert"),
    ):
        result = run_sync(config)

    assert result.status == "failed"
    assert not Path(config.state_file).exists()
    assert Path(config.failed_uploads_dir).exists()


def test_run_sync_success_commits_state(tmp_path):
    import csv

    config = make_config(tmp_path)
    master = Path(config.local_master_copy)
    rows = [
        {
            "External ID": "1",
            "First Name": "Test",
            "Last Name": "User",
            "Phone 1": "9195550100",
        }
    ]
    headers = list(rows[0].keys())
    with master.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    mock_transport = MagicMock()

    with (
        patch("src.pipeline._download_master"),
        patch("src.pipeline.create_transport", mock_transport),
        patch("src.pipeline.send_failure_alert"),
    ):
        result = run_sync(config)

    assert result.status == "success"
    assert result.rows_deleted == 0
    assert Path(config.state_file).exists()
    assert not Path(config.upload_staging_csv).exists()


def test_run_sync_handles_opt_out_delete_in_same_run(tmp_path):
    import csv

    config = make_config(tmp_path)
    master = Path(config.local_master_copy)
    rows = [
        {
            "External ID": "1",
            "First Name": "In",
            "Last Name": "User",
            "Phone 1": "9195550100",
            "Opted In": "TRUE",
        },
        {
            "External ID": "2",
            "First Name": "Out",
            "Last Name": "User",
            "Phone 1": "",
            "Opted In": "FALSE",
        },
    ]
    with master.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    mock_transport = MagicMock()
    transport = mock_transport.return_value

    with (
        patch("src.pipeline._download_master"),
        patch("src.pipeline.create_transport", mock_transport),
        patch("src.pipeline.send_success_alert"),
    ):
        result = run_sync(config)

    assert result.status == "success"
    assert result.rows_uploaded == 1
    assert result.rows_deleted == 1
    transport.upsert_batch.assert_called_once()
    transport.delete_batch.assert_called_once()
    assert Path(config.state_file).exists()


def test_run_sync_success_includes_contact_names_in_alert(tmp_path):
    import csv

    config = make_config(tmp_path)
    master = Path(config.local_master_copy)
    rows = [
        {
            "External ID": "1",
            "First Name": "Jane",
            "Last Name": "Doe",
            "Phone 1": "9195550100",
        },
        {
            "External ID": "2",
            "First Name": "Bad",
            "Last Name": "Row",
            "Phone 1": "invalid",
        },
    ]
    headers = list(rows[0].keys())
    with master.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    mock_transport = MagicMock()
    config = replace(config, teams_notify_on_success=True)

    with (
        patch("src.pipeline._download_master"),
        patch("src.pipeline.create_transport", mock_transport),
        patch("src.pipeline.send_success_alert") as mock_alert,
    ):
        result = run_sync(config)

    assert result.status == "success"
    context = mock_alert.call_args.args[2]
    assert "Jane Doe" in context["Succeeded contacts"]
    assert "Bad Row" in context["Failed contacts"]


def test_download_master_skip_graph_uses_fallback(tmp_path):
    import csv

    config = replace(make_config(tmp_path), skip_graph_download=True)
    fallback = Path(config.local_fallback_csv)
    rows = [
        {
            "External ID": "1",
            "First Name": "Test",
            "Last Name": "User",
            "Phone 1": "9195550100",
        }
    ]
    with fallback.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with patch("src.pipeline.download_delegated_master") as mock_download:
        _download_master(config)
        mock_download.assert_not_called()

    assert Path(config.local_master_copy).exists()


def test_download_master_skip_graph_uses_existing_master(tmp_path):
    import csv

    config = replace(make_config(tmp_path), skip_graph_download=True)
    master = Path(config.local_master_copy)
    rows = [
        {
            "External ID": "1",
            "First Name": "Test",
            "Last Name": "User",
            "Phone 1": "9195550100",
        }
    ]
    with master.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with patch("src.pipeline.download_delegated_master") as mock_download:
        _download_master(config)
        mock_download.assert_not_called()
