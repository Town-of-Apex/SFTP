"""Tests for configuration loading."""



from src.config import load_config





def test_real_values_are_loaded(monkeypatch, tmp_path):
    cache_file = tmp_path / "ms_graph_token_cache.json"
    cache_file.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MS_TENANT_ID", "tenant-123")
    monkeypatch.setenv("MS_CLIENT_ID", "client-123")
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("MS_TOKEN_CACHE_PATH", str(cache_file))
    monkeypatch.setenv("MS_FILE_ID", "file-id-abc")
    monkeypatch.setenv("SFTP_HOST", "sftp.example.com")
    monkeypatch.setenv("SFTP_USERNAME", "user123")

    config = load_config()
    assert config.ms_tenant_id == "tenant-123"
    assert config.ms_file_id == "file-id-abc"
    assert config.graph_configured is True
    assert config.sftp_host == "sftp.example.com"
    assert config.sftp_username == "user123"





def test_empty_ms_values_are_none(monkeypatch):
    monkeypatch.setenv("MS_TENANT_ID", "")
    monkeypatch.setenv("MS_CLIENT_ID", "client-123")
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret-123")

    config = load_config()
    assert config.ms_tenant_id is None
    assert config.graph_configured is False





def test_allow_local_fallback_defaults_false(monkeypatch):

    monkeypatch.delenv("ALLOW_LOCAL_FALLBACK", raising=False)

    config = load_config()

    assert config.allow_local_fallback is False





def test_allow_local_fallback_can_be_enabled(monkeypatch):

    monkeypatch.setenv("ALLOW_LOCAL_FALLBACK", "true")

    config = load_config()

    assert config.allow_local_fallback is True





def test_graph_delegated_configured_with_token_cache_file(monkeypatch, tmp_path):

    cache_file = tmp_path / "ms_graph_token_cache.json"

    cache_file.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("MS_TENANT_ID", "tenant-123")

    monkeypatch.setenv("MS_CLIENT_ID", "client-123")

    monkeypatch.setenv("MS_CLIENT_SECRET", "secret-123")

    monkeypatch.delenv("MS_REFRESH_TOKEN", raising=False)

    monkeypatch.setenv("MS_TOKEN_CACHE_PATH", str(cache_file))

    config = load_config()

    assert config.graph_delegated_configured is True





def test_teams_notify_on_success_defaults_false(monkeypatch):
    monkeypatch.delenv("TEAMS_NOTIFY_ON_SUCCESS", raising=False)
    config = load_config()
    assert config.teams_notify_on_success is False


def test_teams_notify_on_success_can_be_enabled(monkeypatch):
    monkeypatch.setenv("TEAMS_NOTIFY_ON_SUCCESS", "true")
    config = load_config()
    assert config.teams_notify_on_success is True


def test_skip_graph_download_defaults_false(monkeypatch):

    monkeypatch.delenv("SKIP_GRAPH_DOWNLOAD", raising=False)

    config = load_config()

    assert config.skip_graph_download is False





def test_skip_graph_download_can_be_enabled(monkeypatch):

    monkeypatch.setenv("SKIP_GRAPH_DOWNLOAD", "true")

    config = load_config()

    assert config.skip_graph_download is True





def test_sftp_delete_dir_defaults_to_delete(monkeypatch):
    monkeypatch.delenv("SFTP_REMOTE_DELETE_DIR", raising=False)
    config = load_config()
    assert config.sftp_remote_delete_dir == "/delete"


