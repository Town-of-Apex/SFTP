"""Environment configuration loading and validation."""



from __future__ import annotations



import os

from dataclasses import dataclass





DEFAULT_MS_FILE_PATH = "Projects/Emergency Alerts/Emergency_Alert_Registrations.csv"





def _env_str(name: str) -> str | None:

    raw = os.getenv(name)

    if not raw:

        return None

    return raw.strip().strip("'\"")



def _env_bool(name: str, default: bool = False) -> bool:

    raw = os.getenv(name, str(default)).lower()

    return raw in ("1", "true", "yes", "on")





@dataclass(frozen=True)

class Config:

    # Microsoft Graph

    ms_tenant_id: str | None

    ms_client_id: str | None

    ms_client_secret: str | None

    ms_drive_id: str | None

    ms_drive_owner: str | None

    ms_refresh_token: str | None

    ms_token_cache_path: str

    ms_file_id: str | None

    ms_file_path: str



    # Everbridge transport

    everbridge_transport: str



    # SFTP

    sftp_host: str

    sftp_port: int

    sftp_username: str

    sftp_key_path: str

    sftp_remote_dir: str

    sftp_remote_filename: str

    sftp_remote_delete_dir: str

    sftp_remote_delete_filename: str

    delete_staging_csv: str



    # Local paths

    state_file: str

    local_master_copy: str

    upload_staging_csv: str

    sent_files_dir: str

    failed_uploads_dir: str

    rejected_rows_csv: str

    local_fallback_csv: str



    # Behavior

    allow_local_fallback: bool

    skip_graph_download: bool

    graph_max_retries: int

    sftp_max_retries: int

    sftp_timeout_seconds: int



    # Scheduler

    sync_timezone: str

    sync_day_of_week: str

    sync_hour: int

    sync_minute: int



    # Alerting

    teams_webhook_url: str | None

    teams_notify_on_success: bool

    smtp_host: str | None

    smtp_port: int

    smtp_username: str | None

    smtp_password: str | None

    alert_email_to: str | None

    alert_email_from: str | None



    @property
    def graph_configured(self) -> bool:
        """Delegated Graph auth is ready for pipeline download."""
        return self.graph_delegated_configured and bool(
            self.ms_file_id or self.ms_file_path
        )

    @property
    def graph_delegated_configured(self) -> bool:

        if not all([self.ms_tenant_id, self.ms_client_id, self.ms_client_secret]):

            return False

        if self.ms_refresh_token:

            return True

        return os.path.isfile(self.ms_token_cache_path) and os.path.getsize(
            self.ms_token_cache_path
        ) > 0





def load_config() -> Config:

    ms_file_path = os.getenv("MS_FILE_PATH", DEFAULT_MS_FILE_PATH)

    sftp_remote_filename = os.getenv("SFTP_REMOTE_FILENAME", ms_file_path)



    return Config(

        ms_tenant_id=_env_str("MS_TENANT_ID"),

        ms_client_id=_env_str("MS_CLIENT_ID"),

        ms_client_secret=_env_str("MS_CLIENT_SECRET"),

        ms_drive_id=_env_str("MS_DRIVE_ID"),

        ms_drive_owner=_env_str("MS_DRIVE_OWNER"),

        ms_refresh_token=_env_str("MS_REFRESH_TOKEN"),

        ms_token_cache_path=os.getenv("MS_TOKEN_CACHE_PATH", "ms_graph_token_cache.json"),

        ms_file_id=_env_str("MS_FILE_ID"),

        ms_file_path=ms_file_path,

        everbridge_transport=os.getenv("EVERBRIDGE_TRANSPORT", "sftp"),

        sftp_host=os.getenv("SFTP_HOST", "sftp-aws-us3.everbridge.net"),

        sftp_port=int(os.getenv("SFTP_PORT", "22")),

        sftp_username=os.getenv("SFTP_USERNAME", "892807736726354"),

        sftp_key_path=os.getenv("SFTP_KEY_PATH", "Apex.key"),

        sftp_remote_dir=os.getenv("SFTP_REMOTE_DIR", "/update"),

        sftp_remote_filename=sftp_remote_filename,

        sftp_remote_delete_dir=os.getenv("SFTP_REMOTE_DELETE_DIR", "/delete"),

        sftp_remote_delete_filename=os.getenv(
            "SFTP_REMOTE_DELETE_FILENAME", sftp_remote_filename
        ),

        delete_staging_csv=os.getenv("DELETE_STAGING_CSV", "everbridge_delete.csv"),

        state_file=os.getenv("STATE_FILE", "sync_state.json"),

        local_master_copy=os.getenv("LOCAL_MASTER_COPY", "master_download.csv"),

        upload_staging_csv=os.getenv("UPLOAD_STAGING_CSV", "everbridge_upload.csv"),

        sent_files_dir=os.getenv("SENT_FILES_DIR", "sent_files"),

        failed_uploads_dir=os.getenv("FAILED_UPLOADS_DIR", "failed_uploads"),

        rejected_rows_csv=os.getenv("REJECTED_ROWS_CSV", "rejected_rows.csv"),

        local_fallback_csv=os.getenv(

            "LOCAL_FALLBACK_CSV", DEFAULT_MS_FILE_PATH

        ),

        allow_local_fallback=_env_bool("ALLOW_LOCAL_FALLBACK", False),

        skip_graph_download=_env_bool("SKIP_GRAPH_DOWNLOAD", False),

        graph_max_retries=int(os.getenv("GRAPH_MAX_RETRIES", "3")),

        sftp_max_retries=int(os.getenv("SFTP_MAX_RETRIES", "3")),

        sftp_timeout_seconds=int(os.getenv("SFTP_TIMEOUT_SECONDS", "60")),

        sync_timezone=os.getenv("SYNC_TIMEZONE", "America/New_York"),

        sync_day_of_week=os.getenv("SYNC_DAY_OF_WEEK", "fri"),

        sync_hour=int(os.getenv("SYNC_HOUR", "10")),

        sync_minute=int(os.getenv("SYNC_MINUTE", "0")),

        teams_webhook_url=os.getenv("TEAMS_WEBHOOK_URL") or None,

        teams_notify_on_success=_env_bool("TEAMS_NOTIFY_ON_SUCCESS", False),

        smtp_host=os.getenv("SMTP_HOST") or None,

        smtp_port=int(os.getenv("SMTP_PORT", "587")),

        smtp_username=os.getenv("SMTP_USERNAME") or None,

        smtp_password=os.getenv("SMTP_PASSWORD") or None,

        alert_email_to=os.getenv("ALERT_EMAIL_TO") or None,

        alert_email_from=os.getenv("ALERT_EMAIL_FROM") or None,

    )


