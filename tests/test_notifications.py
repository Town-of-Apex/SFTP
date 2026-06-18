"""Tests for failure notifications."""

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

from src.config import Config
from src.notifications import (
    _build_teams_adaptive_card_payload,
    send_delete_alert,
    send_failure_alert,
    send_success_alert,
)


def _minimal_config(webhook: str | None = "https://example.test/hook") -> Config:
    return Config(
        ms_tenant_id=None,
        ms_client_id=None,
        ms_client_secret=None,
        ms_drive_id=None,
        ms_drive_owner=None,
        ms_refresh_token=None,
        ms_token_cache_path="cache.json",
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
        teams_webhook_url=webhook,
        teams_notify_on_success=False,
        smtp_host=None,
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        alert_email_to=None,
        alert_email_from=None,
    )


def test_teams_payload_includes_attachments_array():
    payload = _build_teams_adaptive_card_payload("Everbridge Sync Failure\nType: auth")
    assert payload["type"] == "message"
    assert isinstance(payload["attachments"], list)
    assert len(payload["attachments"]) == 1
    assert (
        payload["attachments"][0]["contentType"]
        == "application/vnd.microsoft.card.adaptive"
    )
    assert payload["attachments"][0]["content"]["type"] == "AdaptiveCard"


def test_send_failure_alert_posts_adaptive_card():
    config = _minimal_config()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("src.notifications.requests.post", return_value=mock_response) as mock_post:
        send_failure_alert(config, "auth", "token expired", {"sync_run_id": "abc"})

    posted = json.loads(mock_post.call_args.kwargs["data"])
    assert "attachments" in posted
    assert posted["attachments"][0]["content"]["body"][0]["text"] == "Everbridge Sync Failure"


def test_send_success_alert_skipped_when_disabled():
    config = _minimal_config()

    with patch("src.notifications.requests.post") as mock_post:
        send_success_alert(config, "success", {"rows_uploaded": 3})

    mock_post.assert_not_called()


def test_send_success_alert_posts_when_enabled():
    config = replace(_minimal_config(), teams_notify_on_success=True)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("src.notifications.requests.post", return_value=mock_response) as mock_post:
        send_success_alert(config, "success", {"rows_uploaded": 3})

    posted = json.loads(mock_post.call_args.kwargs["data"])
    assert posted["attachments"][0]["content"]["body"][0]["text"] == "Everbridge Sync Complete"


def test_send_delete_alert_posts_restore_note():
    config = _minimal_config()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("src.notifications.requests.post", return_value=mock_response) as mock_post:
        send_delete_alert(config, "success", {"contact": "John Smith"})

    posted = json.loads(mock_post.call_args.kwargs["data"])
    body_text = " ".join(
        block["text"] for block in posted["attachments"][0]["content"]["body"]
    )
    assert "Everbridge Contact Deleted" in body_text
    assert "John Smith" in body_text
    assert "30 days" in body_text
