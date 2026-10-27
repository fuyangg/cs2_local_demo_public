import pytest

from cs2ta.runtime import settings, version_label


def test_dev_prod_defaults_are_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("CS2TA_ENV", raising=False)
    dev, prod = settings(tmp_path), settings(tmp_path, "prod")
    assert dev.environment == "dev"
    assert (dev.port, prod.port) == (8501, 8502)
    assert dev.data_dir != prod.data_dir
    assert dev.map_cache_dir != prod.map_cache_dir
    assert dev.data_dir == tmp_path / "data/dev"


def test_local_configuration_applies_only_to_the_selected_environment(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/local.dev.toml").write_text('port = 8601\ndata_dir = "data/custom-dev"\n')
    assert settings(tmp_path, "dev").port == 8601
    assert settings(tmp_path, "prod").port == 8502
    assert settings(tmp_path, "dev").data_dir == tmp_path / "data/custom-dev"


def test_invalid_environment_and_overlapping_cache_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="dev 或 prod"):
        settings(tmp_path, "staging")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/local.dev.toml").write_text('data_dir="same"\nmap_cache_dir="same/maps"\n')
    with pytest.raises(ValueError, match="覆盖"):
        settings(tmp_path)


def test_prod_version_label_uses_the_installed_release(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2TA_ENV", "prod")
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime/deployment.json").write_text('{"version":"v1.2.3"}')
    assert version_label(tmp_path).startswith("v1.2.3 · ")
    monkeypatch.delenv("CS2TA_ENV")
    assert settings(tmp_path).environment == "prod"
