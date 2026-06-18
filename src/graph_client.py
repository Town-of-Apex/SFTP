"""Microsoft Graph API client for OneDrive file download."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

import msal
import requests

from src.config import Config

logger = logging.getLogger("everbridge-sync.graph")

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
ME_DRIVE_PATH = "/me/drive"

# Delegated scopes for refresh-token / device-code sign-in.
# MSAL adds offline_access, openid, and profile automatically — do not pass them.
GRAPH_DELEGATED_SCOPES = [
    "https://graph.microsoft.com/Files.Read.All",
]

# Local redirect for one-time browser sign-in (confidential auth-code flow).
AUTH_CODE_REDIRECT_URI = "http://localhost:8400"
AUTH_CODE_REDIRECT_PORT = 8400

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DriveContext:
    """Resolved Graph path prefix for a OneDrive drive."""

    base_path: str
    label: str


@dataclass(frozen=True)
class DelegatedTokenResult:
    access_token: str
    refresh_token: str | None = None
    cache_updated: bool = False


def me_drive_context() -> DriveContext:
    return DriveContext(base_path=ME_DRIVE_PATH, label="signed-in user (/me/drive)")


def master_file_basename(file_path: str) -> str:
    """Filename for Graph search (search does not accept folder paths)."""
    return file_path.replace("\\", "/").rstrip("/").split("/")[-1]


def delegated_download_urls(config: Config, context: DriveContext) -> list[tuple[str, str]]:
    """Return download URL candidates in priority order: file ID, then path."""
    candidates: list[tuple[str, str]] = []

    if config.ms_file_id:
        item_path = f"/items/{quote(config.ms_file_id, safe='')}/content"
        candidates.append(
            (
                f"{GRAPH_BASE}{drive_item_path(context, item_path)}",
                f"item id {config.ms_file_id[:16]}...",
            )
        )

    if config.ms_file_path:
        encoded_file = quote(config.ms_file_path, safe="/")
        candidates.append(
            (
                f"{GRAPH_BASE}{drive_item_path(context, f'/root:/{encoded_file}:/content')}",
                f"path {config.ms_file_path}",
            )
        )

    return candidates


def _load_token_cache(cache_path: str) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            cache.deserialize(handle.read())
    return cache


def _persist_token_cache(cache: msal.SerializableTokenCache, cache_path: str) -> bool:
    if not cache.has_state_changed:
        return False
    with open(cache_path, "w", encoding="utf-8") as handle:
        handle.write(cache.serialize())
    return True


def _graph_authority(config: Config) -> str:
    return f"https://login.microsoftonline.com/{config.ms_tenant_id}"


def build_public_client(
    config: Config,
    cache: msal.SerializableTokenCache | None = None,
) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    """Public client for interactive device-code sign-in."""
    if cache is None:
        cache = _load_token_cache(config.ms_token_cache_path)
    app = msal.PublicClientApplication(
        config.ms_client_id,
        authority=_graph_authority(config),
        token_cache=cache,
    )
    return app, cache


def build_confidential_client(
    config: Config,
) -> tuple[msal.ConfidentialClientApplication, msal.SerializableTokenCache]:
    """Confidential client for silent / refresh-token acquisition."""
    cache = _load_token_cache(config.ms_token_cache_path)
    app = msal.ConfidentialClientApplication(
        config.ms_client_id,
        authority=_graph_authority(config),
        client_credential=config.ms_client_secret,
        token_cache=cache,
    )
    return app, cache


def decode_token_payload(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def drive_item_path(context: DriveContext, item_path: str) -> str:
    """Build a path under the resolved drive (item_path starts with /root...)."""
    return f"{context.base_path}{item_path}"


def _is_guid(value: str) -> bool:
    return bool(_GUID_RE.match(value))


def drive_base_candidates(config: Config) -> list[tuple[str, str]]:
    """Return Graph drive base paths to try (legacy app-only client credentials)."""
    candidates: list[tuple[str, str]] = []

    if config.ms_drive_id:
        encoded = quote(config.ms_drive_id, safe="")
        candidates.append(
            (f"/drives/{encoded}", f"drive id {config.ms_drive_id[:16]}...")
        )

    if config.ms_drive_owner:
        owner = config.ms_drive_owner
        if _is_guid(owner):
            candidates.append((f"/users/{owner}/drive", f"user object id {owner}"))
        else:
            candidates.append((f"/users/{owner}/drive", f"UPN {owner}"))

    return candidates


def resolve_drive_context(
    config: Config, token: str, session: requests.Session | None = None
) -> DriveContext | None:
    """Probe candidate drive paths and return the first that responds (app-only)."""
    client = session or requests
    headers = {"Authorization": f"Bearer {token}"}

    for base_path, label in drive_base_candidates(config):
        response = client.get(f"{GRAPH_BASE}{base_path}", headers=headers, timeout=60)
        if response.status_code == 200:
            logger.info("Graph drive resolved via %s (%s)", label, base_path)
            return DriveContext(base_path=base_path, label=label)

        logger.warning(
            "Drive probe failed via %s: %s %s",
            label,
            response.status_code,
            response.text[:200],
        )

    return None


def get_delegated_access_token(config: Config) -> DelegatedTokenResult | None:
    """Acquire a delegated access token using the on-disk MSAL token cache."""
    if not config.graph_delegated_configured:
        logger.warning(
            "Delegated Graph auth is not configured. "
            "Run device login once or set MS_REFRESH_TOKEN / token cache file."
        )
        return None

    app, cache = build_confidential_client(config)
    accounts = app.get_accounts()

    for attempt in range(1, config.graph_max_retries + 1):
        result = None

        if accounts:
            result = app.acquire_token_silent(
                GRAPH_DELEGATED_SCOPES,
                account=accounts[0],
            )

        if not result and config.ms_refresh_token:
            result = app.acquire_token_by_refresh_token(
                config.ms_refresh_token,
                scopes=GRAPH_DELEGATED_SCOPES,
            )

        if result and "access_token" in result:
            cache_updated = _persist_token_cache(cache, config.ms_token_cache_path)
            if cache_updated:
                logger.info("Persisted rotated Graph tokens to %s", config.ms_token_cache_path)
            return DelegatedTokenResult(
                access_token=result["access_token"],
                refresh_token=result.get("refresh_token"),
                cache_updated=cache_updated,
            )

        error = (result or {}).get("error", "unknown")
        description = (result or {}).get("error_description", "no token returned")
        logger.error(
            "Error acquiring delegated token (attempt %s/%s): %s — %s",
            attempt,
            config.graph_max_retries,
            error,
            description,
        )
        if attempt < config.graph_max_retries:
            time.sleep(2 ** attempt)

    return None


def _capture_localhost_auth_response(
    port: int, timeout: int = 300
) -> dict[str, str] | None:
    """Wait for Azure to redirect back with ?code=... or ?error=..."""
    captured: dict[str, str] = {}

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured.update(
                {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><p>Sign-in complete. You can close this tab "
                b"and return to the terminal.</p></body></html>"
            )

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), RedirectHandler)
    server.timeout = 1
    deadline = time.time() + timeout
    while time.time() < deadline and "code" not in captured and "error" not in captured:
        server.handle_request()

    return captured or None


def _finish_delegated_login(
    app: msal.ClientApplication,
    cache: msal.SerializableTokenCache,
    config: Config,
    result: dict,
) -> bool:
    if "access_token" not in result:
        print(
            f"ERROR: Login failed: {result.get('error')} — "
            f"{result.get('error_description')}"
        )
        return False

    accounts = app.get_accounts()
    username = accounts[0].get("username") if accounts else "(unknown)"
    print(f"Signed in as: {username}")

    if not _persist_token_cache(cache, config.ms_token_cache_path):
        print("ERROR: Login succeeded but the token cache was not written.")
        return False

    print(f"Token cache saved to: {config.ms_token_cache_path}")
    return True


def _run_browser_auth_code_login(config: Config) -> bool:
    """Browser sign-in via auth-code flow (works with confidential app registrations)."""
    if not all([config.ms_tenant_id, config.ms_client_id, config.ms_client_secret]):
        print(
            "ERROR: Set MS_TENANT_ID, MS_CLIENT_ID, and MS_CLIENT_SECRET "
            "before sign-in."
        )
        return False

    print(f"Tenant:       {config.ms_tenant_id}")
    print(f"Client ID:    {config.ms_client_id}")
    print(f"Cache file:   {config.ms_token_cache_path}")
    print(f"Scopes:       {GRAPH_DELEGATED_SCOPES}")
    print(f"Redirect URI: {AUTH_CODE_REDIRECT_URI}")
    print("Flow:         auth code (ConfidentialClientApplication + browser)")
    print(
        "\nAzure prerequisite: add the redirect URI above under\n"
        "  App registration → Authentication → Web → Redirect URIs\n"
    )

    app, cache = build_confidential_client(config)
    print("Initiating auth code flow...")
    flow = app.initiate_auth_code_flow(
        scopes=GRAPH_DELEGATED_SCOPES,
        redirect_uri=AUTH_CODE_REDIRECT_URI,
    )
    if "auth_uri" not in flow:
        print(f"ERROR: Auth code flow failed to start: {flow}")
        return False

    print("Opening browser for sign-in...")
    print(f"Manual URL: {flow['auth_uri']}")
    webbrowser.open(flow["auth_uri"])
    print(
        f"\nWaiting for redirect to {AUTH_CODE_REDIRECT_URI} "
        f"(timeout 5 min)..."
    )

    auth_response = _capture_localhost_auth_response(AUTH_CODE_REDIRECT_PORT)
    if auth_response is None:
        print("ERROR: Timed out waiting for browser redirect.")
        return False
    if "error" in auth_response:
        print(
            f"ERROR: Azure returned: {auth_response['error']} — "
            f"{auth_response.get('error_description', '')}"
        )
        return False

    print("Authorization code received, exchanging for tokens...")
    result = app.acquire_token_by_auth_code_flow(flow, auth_response)
    return _finish_delegated_login(app, cache, config, result)


def _run_device_code_login(config: Config) -> bool:
    """Device-code sign-in (requires Azure 'Allow public client flows' = Yes)."""
    if not all([config.ms_tenant_id, config.ms_client_id]):
        print("ERROR: Set MS_TENANT_ID and MS_CLIENT_ID before device login.")
        return False

    print(f"Tenant:     {config.ms_tenant_id}")
    print(f"Client ID:  {config.ms_client_id}")
    print(f"Cache file: {config.ms_token_cache_path}")
    print(f"Scopes:     {GRAPH_DELEGATED_SCOPES}")
    print("Flow:       device code (PublicClientApplication)")
    print(
        "\nAzure prerequisite: App registration → Authentication →\n"
        "  Advanced settings → Allow public client flows = Yes\n"
    )

    app, cache = build_public_client(config)
    print("Initiating device code flow...")
    flow = app.initiate_device_flow(scopes=GRAPH_DELEGATED_SCOPES)
    if "user_code" not in flow:
        print(f"ERROR: Device flow failed to start: {flow}")
        return False

    print("\n--- Sign in at the URL below ---")
    print(flow["message"])
    print("\nWaiting for sign-in to complete...")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        description = result.get("error_description", "")
        print(
            f"ERROR: Device login failed: {result.get('error')} — {description}"
        )
        if "7000218" in description:
            print(
                "\nThis app registration requires a client secret for token exchange.\n"
                "Set MS_CLIENT_SECRET in .env and re-run — browser auth-code flow\n"
                f"will be used instead (redirect URI: {AUTH_CODE_REDIRECT_URI})."
            )
        return False

    return _finish_delegated_login(app, cache, config, result)


def run_device_code_login(config: Config) -> bool:
    """One-time interactive sign-in; persists tokens to the MSAL cache file."""
    if config.ms_client_secret:
        return _run_browser_auth_code_login(config)
    return _run_device_code_login(config)


def _download_from_url(
    config: Config,
    url: str,
    label: str,
    headers: dict[str, str],
) -> bool:
    logger.info("Downloading master CSV via %s...", label)

    for attempt in range(1, config.graph_max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=120)
        except requests.RequestException as exc:
            logger.error(
                "Download request failed via %s (attempt %s/%s): %s",
                label,
                attempt,
                config.graph_max_retries,
                exc,
            )
            if attempt < config.graph_max_retries:
                time.sleep(2 ** attempt)
            continue

        if response.status_code == 200:
            with open(config.local_master_copy, "wb") as handle:
                handle.write(response.content)
            logger.info(
                "Download complete via %s (%s bytes).", label, len(response.content)
            )
            return True

        logger.error(
            "Failed to download via %s (attempt %s/%s): %s - %s",
            label,
            attempt,
            config.graph_max_retries,
            response.status_code,
            response.text[:500],
        )
        if response.status_code not in RETRYABLE_STATUS_CODES:
            break
        if attempt < config.graph_max_retries:
            time.sleep(2 ** attempt)

    return False


def download_delegated_master(config: Config) -> bool:
    """Download the master CSV using delegated /me/drive auth (ID first, then path)."""
    if not config.graph_delegated_configured:
        logger.warning(
            "Delegated Graph auth is not configured. "
            "Run explore_onedrive.py --device-login once on the host."
        )
        return False

    token_result = get_delegated_access_token(config)
    if not token_result:
        return False

    context = me_drive_context()
    headers = {"Authorization": f"Bearer {token_result.access_token}"}
    urls = delegated_download_urls(config, context)
    if not urls:
        logger.error("No download target configured (set MS_FILE_ID and/or MS_FILE_PATH).")
        return False

    for url, label in urls:
        if _download_from_url(config, url, label, headers):
            return True

    return False


def get_access_token(config: Config) -> str | None:
    """Legacy app-only token (client credentials). Used by pipeline until migrated."""
    if not config.graph_configured:
        logger.warning(
            "Microsoft Graph is not fully configured. "
            "Set MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, and "
            "MS_DRIVE_ID and/or MS_DRIVE_OWNER."
        )
        return None

    authority = f"https://login.microsoftonline.com/{config.ms_tenant_id}"
    app = msal.ConfidentialClientApplication(
        config.ms_client_id,
        authority=authority,
        client_credential=config.ms_client_secret,
    )

    for attempt in range(1, config.graph_max_retries + 1):
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" in result:
            return result["access_token"]

        logger.error(
            "Error acquiring token (attempt %s/%s): %s",
            attempt,
            config.graph_max_retries,
            result.get("error_description"),
        )
        if attempt < config.graph_max_retries:
            time.sleep(2 ** attempt)

    return None


def download_from_onedrive(config: Config, token: str | None) -> bool:
    """Legacy app-only download. Prefer download_delegated_master for production."""
    if not token:
        return False

    context = resolve_drive_context(config, token)
    if not context:
        logger.error(
            "Could not access OneDrive via any configured path "
            "(MS_DRIVE_ID / MS_DRIVE_OWNER)."
        )
        return False

    encoded_file = quote(config.ms_file_path, safe="/")
    url = f"{GRAPH_BASE}{drive_item_path(context, f'/root:/{encoded_file}:/content')}"
    return _download_from_url(
        config,
        url,
        context.label,
        {"Authorization": f"Bearer {token}"},
    )
