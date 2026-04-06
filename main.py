import os
import json
import csv
import hashlib
import msal
import requests
import paramiko
import shutil
from pathlib import Path
from datetime import datetime

# --- CONFIG (LOAD FROM ENV) ---
# Microsoft Graph Config
TENANT_ID = os.getenv("MS_TENANT_ID")
CLIENT_ID = os.getenv("MS_CLIENT_ID")
CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET")
DRIVE_ID = os.getenv("MS_DRIVE_ID")
FILE_PATH_ON_DRIVE = os.getenv("MS_FILE_PATH", "Emergency_Alert_Registrations(in).csv")

TENANT_ID = TENANT_ID if TENANT_ID != "1c6e531a-a916-4e3f-a5bd-4fa33591e4a8" else None
CLIENT_ID = CLIENT_ID if CLIENT_ID != "acc1d222-30e304f530864c-46f741b082ef" else None
CLIENT_SECRET = CLIENT_SECRET if CLIENT_SECRET != "8f6fc069-aa5e-4e3b-b550-547a76007ca1" else None
DRIVE_ID = DRIVE_ID if DRIVE_ID != "ENTER_DRIVE_ID_HERE" else None

# SFTP Config
HOST = "sftp-aws-us3.everbridge.net"
PORT = 22
USERNAME = "892807736726354"
KEY_PATH = "Apex.key"
REMOTE_DIR = "/update"

# Local State & History
STATE_FILE = "sync_state.json"
LOCAL_MASTER_COPY = "master_download.csv"
UPLOAD_STAGING_CSV = "everbridge_upload.csv"
SENT_FILES_DIR = "sent_files"


def get_access_token():
    """Gets an access token for Microsoft Graph API using Client Credentials Flow."""
    if not CLIENT_ID or "ENTER" in CLIENT_ID:
        print("Microsoft Client ID not set. Skipping MS Graph steps.")
        return None

    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    app = msal.ConfidentialClientApplication(
        CLIENT_ID, authority=authority, client_secret=CLIENT_SECRET
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

    if "access_token" in result:
        return result["access_token"]
    else:
        print(f"Error acquiring token: {result.get('error_description')}")
        return None


def download_from_onedrive(token):
    """Downloads the file from OneDrive using MS Graph API."""
    if not token:
        return False

    print(f"Downloading {FILE_PATH_ON_DRIVE} from OneDrive...")
    url = f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/root:/{FILE_PATH_ON_DRIVE}:/content"
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        with open(LOCAL_MASTER_COPY, "wb") as f:
            f.write(response.content)
        print("Download complete.")
        return True
    else:
        print(f"Failed to download file: {response.status_code} - {response.text}")
        return False


def get_row_signature(row):
    """Creates a unique hash of the row content to identify unique submissions/updates."""
    row_str = json.dumps(row, sort_keys=True)
    return hashlib.sha256(row_str.encode()).hexdigest()


def filter_new_registrants():
    """Compares the master file with sync_state.json to identify new/updated rows."""
    if not os.path.exists(LOCAL_MASTER_COPY):
        print(f"No master file found locally ({LOCAL_MASTER_COPY}). Skipping filter.")
        return 0

    # Load previously processed signatures
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                processed_state = json.load(f)
                # Map signatures to a set for fast lookup
                processed_signatures = {entry['signature'] for entry in processed_state}
            except json.JSONDecodeError:
                processed_signatures = set()
                processed_state = []
    else:
        processed_state = []
        processed_signatures = set()

    new_rows = []
    updated_state = list(processed_state)

    # Read the master file and find new rows
    with open(LOCAL_MASTER_COPY, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        
        for row in reader:
            sig = get_row_signature(row)
            if sig not in processed_signatures:
                new_rows.append(row)
                # Track this row in state for next time
                updated_state.append({
                    "signature": sig,
                    "processed_at": datetime.now().isoformat(),
                    "row_data": row # Keep a copy as requested
                })
                processed_signatures.add(sig)

    if not new_rows:
        print("No new/updated rows found.")
        return 0

    # Write new rows to staging CSV for upload
    with open(UPLOAD_STAGING_CSV, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(new_rows)

    # Save the updated state file
    with open(STATE_FILE, 'w') as f:
        json.dump(updated_state, f, indent=2)

    print(f"Found {len(new_rows)} new/updated entries.")
    return len(new_rows)


def upload_to_everbridge():
    """Uploads specifically the new-rows CSV to the Everbridge SFTP server."""
    if not os.path.exists(UPLOAD_STAGING_CSV):
        return False

    print(f"Connecting to Everbridge SFTP at {HOST}...")
    key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
    transport = paramiko.Transport((HOST, PORT))
    transport.connect(username=USERNAME, pkey=key)
    sftp = paramiko.SFTPClient.from_transport(transport)

    remote_path = f"{REMOTE_DIR}/{FILE_PATH_ON_DRIVE}"
    print(f"Uploading {len(list(csv.reader(open(UPLOAD_STAGING_CSV)))) - 1} rows to {remote_path}...")
    
    sftp.put(UPLOAD_STAGING_CSV, remote_path)
    
    sftp.close()
    transport.close()
    print("Everbridge upload complete ✅")
    return True


def archive_upload():
    """Moves the successfully uploaded file to a timestamped archive."""
    if not os.path.exists(SENT_FILES_DIR):
        os.makedirs(SENT_FILES_DIR)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_name = f"upload_{timestamp}.csv"
    archive_path = os.path.join(SENT_FILES_DIR, archive_name)
    
    if os.path.exists(UPLOAD_STAGING_CSV):
        shutil.move(UPLOAD_STAGING_CSV, archive_path)
        print(f"Archived upload to: {archive_path}")


def main():
    print(f"--- SYNC START: {datetime.now()} ---")
    
    # 1. Auth & Download (or local fallback for testing)
    token = get_access_token()
    download_success = download_from_onedrive(token)
    
    if not download_success:
        # Check if local dummy file exists for manual testing
        dummy_file = "Emergency_Alert_Registrations(in).csv"
        if os.path.exists(dummy_file):
            print(f"Graph API not ready. Using local dummy file '{dummy_file}' for testing.")
            shutil.copy(dummy_file, LOCAL_MASTER_COPY)
        else:
            print("Neither Graph API nor local dummy file available. Sync aborted.")
            return

    # 2. Filter 
    new_count = filter_new_registrants()
    
    # 3. Upload and Archive only if there's data
    if new_count > 0:
        if upload_to_everbridge():
            archive_upload()
    else:
        print("Sync complete: No action required.")
    
    print(f"--- SYNC END: {datetime.now()} ---")

if __name__ == "__main__":
    main()