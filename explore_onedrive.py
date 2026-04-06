import os
import requests
import msal
import json

# --- CREDENTIALS (Sourced from main.py) ---
# These are the same variables you have in main.py. 
# Fill these in or make sure you have the environment variables set.
TENANT_ID = os.getenv("MS_TENANT_ID", "1c6e531a-a916-4e3f-a5bd-4fa33591e4a8")
CLIENT_ID = os.getenv("MS_CLIENT_ID", "acc1d222-30e3-4f53-46f741b082ef")
CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "8f6fc069-aa5e-4e3b-b550-547a76007ca1")
DRIVE_ID = os.getenv("MS_DRIVE_ID", "") # We'll try to find this if it's empty

# Note: If you're using Client Credentials flow (ConfidentialClientApplication), 
# your app needs "Files.Read.All" or similar application-level permissions.

def get_access_token():
    """Gets an access token for Microsoft Graph API using Client Credentials Flow."""
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    app = msal.ConfidentialClientApplication(
        CLIENT_ID, authority=authority, client_credential=CLIENT_SECRET
    )
    # Using the .default scope for client credentials flow
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

    if "access_token" in result:
        return result["access_token"]
    else:
        print("--- AUTH ERROR ---")
        print(f"Error: {result.get('error')}")
        print(f"Description: {result.get('error_description')}")
        return None

def check_connection_basics(token):
    """Checks the most basic connection points to confirm Graph API is reachable."""
    print("\n--- BASIC CONNECTION CHECKS ---")
    headers = {"Authorization": f"Bearer {token}"}
    
    # Check 1: Organization Info (Confirm we can see our own tenant)
    org_url = "https://graph.microsoft.com/v1.0/organization"
    org_resp = requests.get(org_url, headers=headers)
    
    if org_resp.status_code == 200:
        org_data = org_resp.json().get("value", [{}])[0]
        print(f"✅ Success: Connected to Tenant: {org_data.get('displayName', 'Unknown')}")
        print(f"   Tenant ID: {org_data.get('id')}")
    else:
        print(f"❌ Failed to reach /organization: {org_resp.status_code}")
        print("   (This usually means you need 'Organization.Read.All' permission)")
    # Check 2: Try to list Users (Basic directory check)
    users_url = "https://graph.microsoft.com/v1.0/users?$top=1"
    users_resp = requests.get(users_url, headers=headers)
    
    if users_resp.status_code == 200:
        print(f"✅ Success: Can see users in the directory.")
    else:
        print(f"❌ Failed to reach /users: {users_resp.status_code}")
        print("   (This usually means you need 'User.Read.All' permission)")

def list_drives(token):
    """Lists all drives the application has access to."""
    print("\n--- LISTING DRIVES ---")
    url = "https://graph.microsoft.com/v1.0/drives"
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        drives = response.json().get("value", [])
        if not drives:
            print("No drives found. Check application permissions (e.g., Files.Read.All).")
            return []
        
        for drive in drives:
            print(f"  Drive Name: {drive.get('name', 'N/A')}")
            print(f"  Drive ID:   {drive.get('id')}")
            print(f"  Type:       {drive.get('driveType')}")
            print("-" * 30)
        return drives
    else:
        print(f"Failed to list drives: {response.status_code} - {response.text}")
        return []

def search_for_file(token, drive_id, filename):
    """Searches for a specific file in a given drive."""
    print(f"\n--- SEARCHING FOR '{filename}' IN DRIVE: {drive_id} ---")
    # Using the search endpoint
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/search(q='{filename}')"
    headers = {"Authorization": f"Bearer {token}"}
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        items = response.json().get("value", [])
        if not items:
            print(f"No files matching '{filename}' found in this drive.")
            return
        
        for item in items:
            print(f"  Match:       {item.get('name')}")
            print(f"  ID:          {item.get('id')}")
            # Get the path (the 'parentReference' helps, but doesn't give a full string easily)
            parent = item.get('parentReference', {})
            print(f"  Parent ID:   {parent.get('id')}")
            print(f"  Parent Path: {parent.get('path', 'N/A')}")
            if 'file' in item:
                print(f"  Size:        {item.get('size')} bytes")
            print("-" * 30)
    else:
        print(f"Search failed: {response.status_code} - {response.text}")

def main():
    print("OneDrive / Graph API Explorer Tool")
    print("=" * 40)
    
    token = get_access_token()
    if not token:
        return

    # 0. Check connection basics
    check_connection_basics(token)

    # 1. Show available drives
    drives = list_drives(token)
    
    # 2. Try to search if we have a filename
    filename = "Emergency_Alert_Registrations(in).csv" # Default name from main.py
    
    if drives:
        # If user didn't provide a DRIVE_ID, or it's not in the list, offer to use the first one
        target_drive = DRIVE_ID
        if not target_drive or target_drive == "ENTER_DRIVE_ID_HERE":
            target_drive = drives[0].get('id')
            print(f"\nUsing the first available drive: {target_drive}")
        
        search_for_file(token, target_drive, filename)
    else:
        print("\nNo drives available to search.")

if __name__ == "__main__":
    main()
