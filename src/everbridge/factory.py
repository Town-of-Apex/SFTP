"""Factory for Everbridge contact transports."""

from __future__ import annotations

from src.config import Config
from src.everbridge.protocol import ContactTransport, EverbridgeTransportError
from src.everbridge.sftp import SftpTransport

SUPPORTED_TRANSPORTS = frozenset({"sftp"})


def create_transport(config: Config) -> ContactTransport:
    transport_name = config.everbridge_transport.lower()

    if transport_name == "sftp":
        return SftpTransport(config)

    if transport_name == "api":
        raise EverbridgeTransportError(
            "EVERBRIDGE_TRANSPORT=api is not implemented yet. "
            "See docs/FUTURE_ARCHITECTURE.md."
        )

    raise EverbridgeTransportError(
        f"Unsupported EVERBRIDGE_TRANSPORT '{config.everbridge_transport}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_TRANSPORTS))}."
    )
