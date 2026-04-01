import paramiko
from pathlib import Path

# --- CONFIG ---
HOST = "sftp-aws-us3.everbridge.net"
PORT = 22
USERNAME = "892807736726354"
KEY_PATH = "Apex.key"
LOCAL_FILE = "Emergency_Alert_Registrations(in).csv"
REMOTE_DIR = "/update"
REMOTE_FILENAME = "Emergency_Alert_Registrations(in).csv"

def connect_to_everbridge():
    # --- LOAD PRIVATE KEY ---
    key = paramiko.RSAKey.from_private_key_file(KEY_PATH)

    # --- CONNECT ---
    transport = paramiko.Transport((HOST, PORT))
    transport.connect(username=USERNAME, pkey=key)

    sftp = paramiko.SFTPClient.from_transport(transport)
    print("Connected to Everbridge")
    return sftp, transport

def check_for_folder(sftp):
    try:
        sftp.stat(REMOTE_DIR)
        print("Folder exists")
    except FileNotFoundError:
        print("Folder does not exist")
        return False
    return True
        

def upload_to_everbridge(sftp):
    # --- UPLOAD ---
    remote_path = f"{REMOTE_DIR}/{REMOTE_FILENAME}"

    print(f"Uploading {LOCAL_FILE} → {remote_path}")

    sftp.put(LOCAL_FILE, remote_path)

    print("Upload complete ✅")
    return True

def close_connection(sftp, transport):
    sftp.close()
    transport.close()
    print("Connection closed")

def main():

    sftp, transport = connect_to_everbridge()
    check_for_folder(sftp)
        
    success = upload_to_everbridge(sftp)
    close_connection(sftp, transport)

if __name__ == "__main__":
    main()