"""Tests for Graph client delegated download."""

from unittest.mock import MagicMock, patch

from src.config import Config
from src.graph_client import delegated_download_urls, download_delegated_master, me_drive_context


def _config(tmp_path) -> Config:
    cache = tmp_path / "cache.json"
    cache.write_text("{}", encoding="utf-8")
    return Config(
        ms_tenant_id="tenant",
        ms_client_id="client",
        ms_client_secret="secret",
        ms_drive_id=None,
        ms_drive_owner=None,
        ms_refresh_token=None,
        ms_token_cache_path=str(cache),
        ms_file_id="01UTJ5X6GVVQ575JHVHFFJFBT4AGWAKOP2",
        ms_file_path="Projects/Emergency Alerts/Emergency_Alert_Registrations.csv",
        everbridge_transport="sftp",
        sftp_host="sftp.example.com",
        sftp_port=22,
        sftp_username="user",
        sftp_key_path="key",
        sftp_remote_dir="/update",
        sftp_remote_filename="upload.csv",
        sftp_remote_delete_dir="/delete",
        sftp_remote_delete_filename="upload.csv",
        delete_staging_csv="delete.csv",
        state_file=str(tmp_path / "state.json"),
        local_master_copy=str(tmp_path / "master.csv"),
        upload_staging_csv=str(tmp_path / "staging.csv"),
        sent_files_dir=str(tmp_path / "sent"),
        failed_uploads_dir=str(tmp_path / "failed"),
        rejected_rows_csv=str(tmp_path / "rejected.csv"),
        local_fallback_csv=str(tmp_path / "fallback.csv"),
        allow_local_fallback=False,
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


def test_delegated_download_urls_prefers_file_id(tmp_path):
    config = _config(tmp_path)
    urls = delegated_download_urls(config, me_drive_context())
    assert len(urls) == 2
    assert "01UTJ5X6GVVQ575JHVHFFJFBT4AGWAKOP2" in urls[0][0]
    assert "Emergency_Alert_Registrations.csv" in urls[1][0]


def test_download_delegated_master_uses_first_successful_url(tmp_path):
    config = _config(tmp_path)
    token_result = MagicMock(access_token="token", refresh_token=None, cache_updated=False)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"col1,col2\n1,2"

    with (
        patch("src.graph_client.get_delegated_access_token", return_value=token_result),
        patch("src.graph_client.requests.get", return_value=mock_response) as mock_get,
    ):
        assert download_delegated_master(config) is True

    assert mock_get.call_count == 1
    assert "01UTJ5X6GVVQ575JHVHFFJFBT4AGWAKOP2" in mock_get.call_args.args[0]
    assert (tmp_path / "master.csv").read_bytes() == b"col1,col2\n1,2"


def test_download_delegated_master_falls_back_to_path(tmp_path):
    config = _config(tmp_path)
    token_result = MagicMock(access_token="token", refresh_token=None, cache_updated=False)

    fail_response = MagicMock(status_code=404, text="not found")
    ok_response = MagicMock(status_code=200, content=b"ok")

    with (
        patch("src.graph_client.get_delegated_access_token", return_value=token_result),
        patch(
            "src.graph_client.requests.get",
            side_effect=[fail_response, ok_response],
        ) as mock_get,
    ):
        assert download_delegated_master(config) is True

    assert mock_get.call_count == 2
    assert "Emergency_Alert_Registrations.csv" in mock_get.call_args.args[0]
