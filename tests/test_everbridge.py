"""Tests for Everbridge transport factory."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from src.config import Config
from src.everbridge import EverbridgeTransportError, create_transport
from src.everbridge.sftp import SftpTransport

def _minimal_config(transport: str) -> Config:
    return Config(
        ms_tenant_id=None,
        ms_client_id=None,
        ms_client_secret=None,
        ms_drive_id=None,
        ms_drive_owner=None,
        ms_refresh_token=None,
        ms_token_cache_path="ms_graph_token_cache.json",
        ms_file_id=None,
        ms_file_path="test.csv",
        everbridge_transport=transport,
        sftp_host="sftp.example.com",
        sftp_port=22,
        sftp_username="user",
        sftp_key_path="key",
        sftp_remote_dir="/update",
        sftp_remote_filename="test.csv",
        sftp_remote_delete_dir="/delete",
        sftp_remote_delete_filename="test.csv",
        delete_staging_csv="delete.csv",
        state_file="state.json",
        local_master_copy="master.csv",
        upload_staging_csv="staging.csv",
        sent_files_dir="sent",
        failed_uploads_dir="failed",
        rejected_rows_csv="rejected.csv",
        local_fallback_csv="fallback.csv",
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


def test_create_sftp_transport():
    transport = create_transport(_minimal_config("sftp"))
    assert isinstance(transport, SftpTransport)


def test_api_transport_not_implemented():
    with pytest.raises(EverbridgeTransportError, match="not implemented"):
        create_transport(_minimal_config("api"))


def test_delete_contact_uploads_to_delete_dir(tmp_path):
    config = replace(
        _minimal_config("sftp"),
        local_master_copy=str(tmp_path / "master.csv"),
        delete_staging_csv=str(tmp_path / "delete.csv"),
    )
    transport = SftpTransport(config)

    with (
        patch.object(transport, "_upload_file") as mock_upload,
        patch(
            "src.everbridge.sftp.load_master_headers",
            return_value=["First Name", "Last Name", "External ID"],
        ),
        patch("src.everbridge.sftp.write_delete_staging_csv") as mock_write,
    ):
        assert transport.delete_contact("12345") is True
        mock_write.assert_called_once()
        mock_upload.assert_called_once()
        assert mock_upload.call_args.args[1] == "/delete/test.csv"


def test_delete_contact_requires_master_csv(tmp_path):
    config = replace(
        _minimal_config("sftp"),
        local_master_copy=str(tmp_path / "missing.csv"),
        delete_staging_csv=str(tmp_path / "delete.csv"),
    )
    transport = SftpTransport(config)

    with patch(
        "src.everbridge.sftp.load_master_headers",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(EverbridgeTransportError, match="Master CSV not found"):
            transport.delete_contact("12345")