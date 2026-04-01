# SFTP Everbridge Uploader (Dockerized)

A containerized system to automate the upload of `Emergency_Alert_Registrations(in).csv` to Everbridge SFTP every Friday at 10:00 AM ET.

## Features
- **Scheduled Uploads**: Automated every Friday at 10 AM ET via APScheduler.
- **Manual Trigger Support**: Easily run an ad-hoc upload while the scheduler is running.
- **Resiliency**: Docker's `restart: unless-stopped` ensures the scheduler survives server reboots.
- **Simplified Deployment**: Just `docker compose up -d` once the remote key is uploaded.

## Prerequisites
- **Docker** and **Docker Compose** installed on your server.
- `Apex.key` (RSA private key for authentication) must be in this directory.
- `Emergency_Alert_Registrations(in).csv` (the file to upload) must be in this directory.

## Deployment Instructions

1.  **Transfer Files**: Copy this repository and your `Apex.key` to your server.
2.  **Start Services**:
    ```bash
    docker compose up -d --build
    ```
    This builds the image, installs dependencies using `uv`, and starts the background scheduler.

3.  **Check Logs**:
    Verify the scheduler is running and ready:
    ```bash
    docker compose logs -f
    ```

## Manual Trigger (Ad-hoc Upload)

To manually trigger an upload (e.g., for testing or urgent updates) while the container is running:
```bash
docker compose exec sftp-uploader uv run python main.py
```
This executes the upload process immediately without affecting the schedule.

## Future Plans (OneDrive/MS Graph)
When migrating to Microsoft Graph API for OneDrive integration, simply update `main.py` and potentially your environment variables in `docker-compose.yml`. The core scheduler and deployment structure will remain unchanged.
