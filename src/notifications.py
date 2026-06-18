"""Teams webhook and optional SMTP email alerts."""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import requests

from src.config import Config

logger = logging.getLogger("everbridge-sync.notifications")


def _format_alert_body(title: str, lines: list[str]) -> str:
    return "\n".join([title, *lines])


def _build_failure_message(
    failure_type: str,
    message: str,
    context: dict[str, Any],
) -> str:
    lines = [f"Type: {failure_type}", f"Message: {message}"]
    for key, value in context.items():
        lines.append(f"{key}: {value}")
    return _format_alert_body("Everbridge Sync Failure", lines)


def _build_success_message(status: str, context: dict[str, Any]) -> str:
    lines = [f"Status: {status}"]
    for key, value in context.items():
        lines.append(f"{key}: {value}")
    return _format_alert_body("Everbridge Sync Complete", lines)


def _build_teams_adaptive_card_payload(text: str) -> dict[str, Any]:
    """Power Automate / Teams flows expect body.attachments as an array."""
    body_blocks: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        if not line and index > 0:
            continue
        body_blocks.append(
            {
                "type": "TextBlock",
                "text": line,
                "wrap": True,
                **({"weight": "Bolder", "size": "Medium"} if index == 0 else {}),
            }
        )

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body_blocks,
                },
            }
        ],
    }


def _send_teams_webhook(config: Config, text: str) -> None:
    if not config.teams_webhook_url:
        return

    payload = _build_teams_adaptive_card_payload(text)
    response = requests.post(
        config.teams_webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    logger.info("Alert sent to Teams webhook.")


def _send_email(config: Config, subject: str, body: str) -> None:
    if not all(
        [
            config.smtp_host,
            config.alert_email_to,
            config.alert_email_from,
        ]
    ):
        return

    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = config.alert_email_from
    email["To"] = config.alert_email_to
    email.set_content(body)

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if config.smtp_username and config.smtp_password:
            smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(email)

    logger.info("Failure alert sent via email to %s.", config.alert_email_to)


def send_failure_alert(
    config: Config,
    failure_type: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    context = context or {}
    text = _build_failure_message(failure_type, message, context)

    try:
        _send_teams_webhook(config, text)
    except Exception as exc:
        logger.error("Failed to send Teams alert: %s", exc)

    try:
        _send_email(config, "Everbridge Sync Failure", text)
    except Exception as exc:
        logger.error("Failed to send email alert: %s", exc)


def send_success_alert(
    config: Config,
    status: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Notify Teams on successful sync when TEAMS_NOTIFY_ON_SUCCESS=true."""
    if not config.teams_notify_on_success:
        return

    context = context or {}
    text = _build_success_message(status, context)

    try:
        _send_teams_webhook(config, text)
    except Exception as exc:
        logger.error("Failed to send Teams success alert: %s", exc)


def _build_delete_message(status: str, context: dict[str, Any]) -> str:
    lines = [f"Status: {status}"]
    for key, value in context.items():
        lines.append(f"{key}: {value}")
    return _format_alert_body("Everbridge Contact Deleted", lines)


def send_delete_alert(
    config: Config,
    status: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Notify Teams when a contact is deleted via admin CLI."""
    context = context or {}
    context.setdefault(
        "note",
        "Deleted contacts can be restored for 30 days following today's deletion.",
    )
    text = _build_delete_message(status, context)

    try:
        _send_teams_webhook(config, text)
    except Exception as exc:
        logger.error("Failed to send Teams delete alert: %s", exc)

    if status != "success":
        try:
            _send_email(config, "Everbridge Contact Delete Failed", text)
        except Exception as exc:
            logger.error("Failed to send delete failure email: %s", exc)
