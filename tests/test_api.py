"""Tests for HTTP delete API and shared delete service."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.config import Config
from src.delete_service import (
    DeleteResult,
    DeleteTransportError,
    MasterUnavailableError,
    delete_external_id,
)


def _minimal_config(tmp_path: Path) -> Config:
    master = tmp_path / "master.csv"
    master.write_text("First Name,Last Name,External ID,END\nJane,Doe,1234,\n", encoding="utf-8")
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
        everbridge_transport="sftp",
        sftp_host="sftp.example.com",
        sftp_port=22,
        sftp_username="user",
        sftp_key_path="key",
        sftp_remote_dir="/update",
        sftp_remote_filename="test.csv",
        sftp_remote_delete_dir="/delete",
        sftp_remote_delete_filename="test.csv",
        delete_staging_csv=str(tmp_path / "delete.csv"),
        state_file=str(tmp_path / "state.json"),
        local_master_copy=str(master),
        upload_staging_csv=str(tmp_path / "staging.csv"),
        sent_files_dir=str(tmp_path / "sent"),
        failed_uploads_dir=str(tmp_path / "failed"),
        rejected_rows_csv=str(tmp_path / "rejected.csv"),
        local_fallback_csv=str(tmp_path / "fallback.csv"),
        allow_local_fallback=False,
        skip_graph_download=True,
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


def test_delete_external_id_dry_run(tmp_path: Path):
    config = _minimal_config(tmp_path)
    result = delete_external_id(config, "1234", dry_run=True, notify=False)
    assert result.dry_run is True
    assert result.employee_id == "1234"
    assert result.contact_name == "Jane Doe"
    assert Path(config.delete_staging_csv).is_file()


def test_delete_external_id_requires_id(tmp_path: Path):
    config = _minimal_config(tmp_path)
    with pytest.raises(ValueError, match="employeeId"):
        delete_external_id(config, "  ", dry_run=True, notify=False)


def test_delete_external_id_master_missing(tmp_path: Path):
    config = replace(
        _minimal_config(tmp_path),
        local_master_copy=str(tmp_path / "missing.csv"),
    )
    with pytest.raises(MasterUnavailableError):
        delete_external_id(config, "1234", dry_run=True, notify=False)


def test_delete_external_id_success(tmp_path: Path):
    config = _minimal_config(tmp_path)
    (tmp_path / "state.json").write_text(
        '[{"external_id": "1234", "signature": "abc", "processed_at": "2026-01-01"}]',
        encoding="utf-8",
    )
    mock_transport = MagicMock()

    def _fake_delete(external_id: str) -> bool:
        Path(config.delete_staging_csv).write_text(
            "First Name,Last Name,External ID,END\n,,1234,\n",
            encoding="utf-8",
        )
        return True

    mock_transport.delete_contact.side_effect = _fake_delete

    with (
        patch("src.delete_service.create_transport", return_value=mock_transport),
        patch("src.delete_service.send_delete_alert") as mock_alert,
    ):
        result = delete_external_id(config, "1234")

    mock_transport.delete_contact.assert_called_once_with("1234")
    assert result.employee_id == "1234"
    assert result.contact_name == "Jane Doe"
    assert result.state_entries_removed == 1
    assert result.archive_path is not None
    assert Path(result.archive_path).is_file()
    mock_alert.assert_called_once()


def test_delete_external_id_transport_error(tmp_path: Path):
    from src.everbridge import EverbridgeTransportError

    config = _minimal_config(tmp_path)
    mock_transport = MagicMock()
    mock_transport.delete_contact.side_effect = EverbridgeTransportError("SFTP down")

    with (
        patch("src.delete_service.create_transport", return_value=mock_transport),
        patch("src.delete_service.send_failure_alert") as mock_fail,
    ):
        with pytest.raises(DeleteTransportError, match="SFTP down"):
            delete_external_id(config, "1234")

    mock_fail.assert_called_once()


@pytest.fixture
def api_client(tmp_path: Path):
    config = _minimal_config(tmp_path)
    with patch("api.load_config", return_value=config):
        from api import app

        yield TestClient(app), config


def test_health(api_client):
    client, _ = api_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_delete_via_http_delete(api_client):
    client, _ = api_client
    expected = DeleteResult(
        employee_id="1234",
        contact_name="Jane Doe",
        archive_path="/tmp/archive.csv",
        state_entries_removed=1,
    )
    with patch("api.delete_external_id", return_value=expected) as mock_delete:
        response = client.delete("/contacts/1234")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deleted"
    assert body["employeeId"] == "1234"
    assert body["contact"] == "Jane Doe"
    mock_delete.assert_called_once()


def test_delete_via_http_post(api_client):
    client, _ = api_client
    expected = DeleteResult(
        employee_id="5678",
        contact_name="Unknown",
        archive_path="/tmp/a.csv",
        state_entries_removed=0,
    )
    with patch("api.delete_external_id", return_value=expected):
        response = client.post("/delete", json={"employeeId": "5678"})

    assert response.status_code == 200
    assert response.json()["employeeId"] == "5678"


def test_delete_http_transport_error(api_client):
    client, _ = api_client
    with patch(
        "api.delete_external_id",
        side_effect=DeleteTransportError("upload failed"),
    ):
        response = client.delete("/contacts/1234")

    assert response.status_code == 502
    assert "upload failed" in response.json()["detail"]
