import pytest

from config import Settings, get_settings, require
from database.session import build_engine, database_url


def test_default_test_settings_do_not_load_the_local_env(monkeypatch) -> None:
    from pydantic_settings.sources import DotEnvSettingsSource

    def forbid_local_file(self, file_path, *args, **kwargs):
        raise AssertionError("Tests must not read the application's .env")

    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", forbid_local_file)
    settings = get_settings()
    assert Settings.model_config["env_file"] is None
    assert settings.database_url is None
    assert settings.jina_api_key is None
    assert settings.groq_api_key is None
    assert settings.openrouter_api_key is None


def test_unmocked_database_access_is_blocked_before_connecting() -> None:
    engine = build_engine("postgresql://fake:fake@invalid.example/not-a-test-db")
    try:
        with pytest.raises(AssertionError, match="Mock database access"):
            engine.connect()
    finally:
        engine.dispose()


def test_require_rejects_missing_values_without_leaking(monkeypatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "secret-value")
    assert require(Settings(_env_file=None).jina_api_key, "JINA_API_KEY") == (  # type: ignore[call-arg]
        "secret-value"
    )
    with pytest.raises(RuntimeError, match="Set JINA_API_KEY") as exc:
        require("  ", "JINA_API_KEY")
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    "name",
    ["OPENROUTER_KEY", "OPEN_ROUTER_KEY", "OPENROUTER_API_KEY"],
)
def test_openrouter_key_aliases_are_loaded_as_secrets(monkeypatch, name) -> None:
    monkeypatch.setenv(name, "fake-openrouter-key")
    settings = Settings()
    assert (
        require(settings.openrouter_api_key, "OPENROUTER_KEY") == "fake-openrouter-key"
    )
    assert "fake-openrouter-key" not in repr(settings)
    assert settings.llm_provider == "openrouter"
    assert settings.llm_model == "nvidia/nemotron-3-super-120b-a12b:free"


def test_database_url_alias_and_secret_are_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    assert Settings(_env_file=None).database_url is None  # type: ignore[call-arg]

    monkeypatch.setenv("DB_URL", "postgresql://fallback@localhost/demo")
    assert database_url() == "postgresql+psycopg://fallback@localhost/demo"

    monkeypatch.setenv("DATABASE_URL", "postgresql://preferred@localhost/demo")
    settings = Settings()
    assert settings.database_url is not None
    assert (
        settings.database_url.get_secret_value()
        == "postgresql://preferred@localhost/demo"
    )
    assert "preferred@localhost" not in repr(settings)


def test_local_env_file_is_loaded_but_process_environment_wins(
    tmp_path,
    monkeypatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://local@localhost/demo\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    local_url = Settings(_env_file=env_file).database_url  # type: ignore[call-arg]
    assert local_url is not None
    assert local_url.get_secret_value() == ("postgresql://local@localhost/demo")
    monkeypatch.setenv("DATABASE_URL", "postgresql://process@localhost/demo")
    process_url = Settings(_env_file=env_file).database_url  # type: ignore[call-arg]
    assert process_url is not None
    assert process_url.get_secret_value() == ("postgresql://process@localhost/demo")
