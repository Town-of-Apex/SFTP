"""OneDrive / Graph API explorer for IT onboarding.

Uses delegated auth (refresh token) with Files.Read.All:
  - GET /me/drive
  - GET /me/drive/root/children
  - GET /me/drive/root:/{folder-path}:/children
  - GET /me/drive/root/search(q='...')
  - GET /me/drive/root:/{file-path}:/content

One-time setup (no refresh token yet):
  uv run python explore_onedrive.py --device-login
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

import requests

from dotenv import load_dotenv

from src.config import DEFAULT_MS_FILE_PATH, Config, load_config
from src.graph_client import (
    GRAPH_BASE,
    DriveContext,
    decode_token_payload,
    drive_item_path,
    get_delegated_access_token,
    master_file_basename,
    me_drive_context,
    run_device_code_login,
)

load_dotenv()


def graph_get(token: str, path: str) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}"}
    return requests.get(f"{GRAPH_BASE}{path}", headers=headers, timeout=60)


def print_response_error(label: str, response: requests.Response) -> None:
    body = response.text[:500]
    print(f"  Failed: {label} — {response.status_code}")
    if body:
        print(f"    {body}")


def print_token_diagnostics(token: str) -> None:
    try:
        data = decode_token_payload(token)
        scopes = data.get("scp", "")
        upn = data.get("upn") or data.get("preferred_username", "(unknown)")
        name = data.get("name", "")
        print(f"  Signed in as: {name} <{upn}>" if name else f"  Signed in as: {upn}")
        print(f"  Delegated scopes (scp): {scopes or '(none)'}")
    except (IndexError, ValueError, TypeError):
        print("  Could not decode token payload.")


def check_drive_metadata(token: str) -> DriveContext | None:
    print("\n--- DRIVE METADATA ---")
    context = me_drive_context()
    response = graph_get(token, context.base_path)

    if response.status_code != 200:
        print_response_error(f"GET {context.base_path}", response)
        return None

    drive = response.json()
    print("Success: can access signed-in user's OneDrive.")
    print(f"  Graph path: {context.base_path}")
    print(f"  Name:       {drive.get('name', 'N/A')}")
    print(f"  ID:         {drive.get('id')}")
    print(f"  Type:       {drive.get('driveType')}")
    web_url = drive.get("webUrl")
    if web_url:
        print(f"  URL:        {web_url}")
    return context


def list_folder(
    token: str, context: DriveContext, folder_path: str = ""
) -> list[dict]:
    label = folder_path or "(drive root)"
    print(f"\n--- LISTING FOLDER: {label} ---")

    if folder_path:
        encoded = quote(folder_path, safe="/")
        path = drive_item_path(context, f"/root:/{encoded}:/children")
    else:
        path = drive_item_path(context, "/root/children")

    response = graph_get(token, path)
    if response.status_code != 200:
        print_response_error(f"list {label}", response)
        return []

    items = response.json().get("value", [])
    if not items:
        print("Folder is empty (or you may not have access to this path).")
        return []

    for item in items:
        kind = "folder" if "folder" in item else "file"
        print(f"  [{kind}] {item.get('name')}")
        if kind == "file":
            print(f"         size: {item.get('size', 'N/A')} bytes")
        parent = item.get("parentReference", {})
        parent_path = parent.get("path", "")
        if parent_path:
            print(f"         path: {parent_path}/{item.get('name')}")
        print("-" * 30)

    return items


def search_for_file(token: str, context: DriveContext, filename: str) -> None:
    search_name = master_file_basename(filename)
    print(f"\n--- SEARCHING FOR '{search_name}' ---")
    encoded_name = quote(search_name, safe="")
    path = drive_item_path(context, f"/root/search(q='{encoded_name}')")
    response = graph_get(token, path)

    if response.status_code != 200:
        print_response_error("search", response)
        return

    items = response.json().get("value", [])
    if not items:
        print(f"No files matching '{search_name}' found in this drive.")
        return

    for item in items:
        print(f"  Match:       {item.get('name')}")
        print(f"  ID:          {item.get('id')}")
        parent = item.get("parentReference", {})
        print(f"  Parent Path: {parent.get('path', 'N/A')}")
        if "file" in item:
            print(f"  Size:        {item.get('size')} bytes")
        print("-" * 30)


def check_target_file_by_id(
    token: str, context: DriveContext, file_id: str, download_to: str | None = None
) -> bool:
    print(f"\n--- TARGET FILE (by ID): {file_id} ---")
    metadata_path = drive_item_path(context, f"/items/{quote(file_id, safe='')}")
    response = graph_get(token, metadata_path)

    if response.status_code != 200:
        print_response_error("GET file metadata by id", response)
        return False

    item = response.json()
    print("Success: file metadata resolved by ID.")
    print(f"  Name: {item.get('name')}")
    print(f"  Size: {item.get('size')} bytes")
    print(f"  ID:   {item.get('id')}")

    content_resp = graph_get(token, f"{metadata_path}/content")
    if content_resp.status_code != 200:
        print_response_error("GET file content by id", content_resp)
        return False

    size = len(content_resp.content)
    print(f"  Download probe: OK ({size} bytes)")

    if download_to:
        Path(download_to).write_bytes(content_resp.content)
        print(f"  Saved to: {download_to}")

    return True


def check_target_file(
    token: str, context: DriveContext, file_path: str, download_to: str | None = None
) -> bool:
    print(f"\n--- TARGET FILE: {file_path} ---")
    encoded = quote(file_path, safe="/")
    metadata_path = drive_item_path(context, f"/root:/{encoded}:")
    response = graph_get(token, metadata_path)

    if response.status_code != 200:
        print_response_error("GET file metadata", response)
        if response.status_code == 404:
            print(
                "  Hint: MS_FILE_PATH must be relative to the drive root, "
                "e.g. 'Projects/Emergency Alerts/Emergency_Alert_Registrations.csv'."
            )
        return False

    item = response.json()
    print("Success: file metadata resolved.")
    print(f"  Name: {item.get('name')}")
    print(f"  Size: {item.get('size')} bytes")
    print(f"  ID:   {item.get('id')}")

    content_path = f"{metadata_path}/content"
    content_resp = graph_get(token, content_path)
    if content_resp.status_code != 200:
        print_response_error("GET file content", content_resp)
        return False

    size = len(content_resp.content)
    print(f"  Download probe: OK ({size} bytes)")

    if download_to:
        Path(download_to).write_bytes(content_resp.content)
        print(f"  Saved to: {download_to}")

    return True


def run_device_login(config: Config) -> None:
    print("OneDrive / Graph — Sign-in (one-time setup)")
    print("=" * 52)
    print(
        "Sign in as the user whose OneDrive holds the master CSV.\n"
        "Delegated permissions required: Files.Read.All (refresh token via MSAL).\n"
        "With MS_CLIENT_SECRET set, a browser window opens (auth-code flow).\n"
        "Register redirect URI http://localhost:8400 in Azure if prompted.\n"
        "Tokens are saved to the MSAL cache file for unattended refresh — "
        "you should not need to sign in again unless IT revokes access.\n"
    )

    if not run_device_code_login(config):
        sys.exit(1)

    print("\n--- SUCCESS ---")
    print(f"Token cache written to: {config.ms_token_cache_path}")
    print(
        "\nFor Docker, mount this file as a persistent volume (see docker-compose.yml).\n"
        "MS_REFRESH_TOKEN in .env is optional — the cache file is the long-term store.\n"
        "\nThen run:  uv run python explore_onedrive.py\n"
        "to verify drive access and CSV download."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Explore OneDrive using delegated Files.Read.All (refresh token).",
    )
    parser.add_argument(
        "--device-login",
        action="store_true",
        help="One-time interactive sign-in to obtain MS_REFRESH_TOKEN.",
    )
    parser.add_argument(
        "--folder",
        metavar="PATH",
        default="",
        help="Optional folder path relative to drive root to list (default: drive root).",
    )
    parser.add_argument(
        "--search",
        metavar="NAME",
        default="",
        help="Filename to search for within the drive (default: MS_FILE_PATH).",
    )
    parser.add_argument(
        "--skip-file-check",
        action="store_true",
        help="Skip the target file metadata/download probe.",
    )
    parser.add_argument(
        "--download-to",
        metavar="PATH",
        default="",
        help="Save the target CSV to this local path after a successful probe.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()

    if args.device_login:
        run_device_login(config)
        return

    print("OneDrive / Graph API Explorer (delegated Files.Read.All)")
    print("=" * 52)

    print(f"Tenant ID:     {config.ms_tenant_id or '(not set)'}")
    print(f"Client ID:     {config.ms_client_id or '(not set)'}")
    print(f"Token cache:   {config.ms_token_cache_path}")
    cache_exists = Path(config.ms_token_cache_path).is_file()
    print(f"  Cache file:  {'present' if cache_exists else 'missing'}")
    print(f"Refresh token: {'set in .env (optional)' if config.ms_refresh_token else 'not in .env (ok if cache exists)'}")
    print(f"File path:     {config.ms_file_path or DEFAULT_MS_FILE_PATH}")
    print(f"File ID:       {config.ms_file_id or '(not set — path fallback only)'}")

    if not config.graph_delegated_configured:
        print(
            "\nDelegated Graph auth is not configured.\n"
            "  1. Set MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET in .env\n"
            "  2. Run:  uv run python explore_onedrive.py --device-login\n"
            "  3. Re-run this script (cache file holds tokens for unattended use)"
        )
        sys.exit(1)

    token_result = get_delegated_access_token(config)
    if not token_result:
        print(
            "\nFailed to acquire access token from cache.\n"
            "If the refresh token was revoked, re-run:  uv run python explore_onedrive.py --device-login"
        )
        sys.exit(1)

    print("\n--- TOKEN ---")
    print("Success: acquired access token (silent refresh from cache).")
    print_token_diagnostics(token_result.access_token)

    if token_result.cache_updated:
        print(
            f"\n  Token cache updated automatically: {config.ms_token_cache_path}"
        )

    context = check_drive_metadata(token_result.access_token)
    if not context:
        sys.exit(1)

    list_folder(token_result.access_token, context, folder_path=args.folder)

    search_name = args.search or config.ms_file_path or DEFAULT_MS_FILE_PATH
    search_for_file(token_result.access_token, context, search_name)

    if not args.skip_file_check:
        download_to = args.download_to or None
        if config.ms_file_id:
            check_target_file_by_id(
                token_result.access_token,
                context,
                config.ms_file_id,
                download_to=download_to,
            )
        file_path = config.ms_file_path or DEFAULT_MS_FILE_PATH
        check_target_file(
            token_result.access_token,
            context,
            file_path,
            download_to=download_to if not config.ms_file_id else None,
        )


if __name__ == "__main__":
    main()
