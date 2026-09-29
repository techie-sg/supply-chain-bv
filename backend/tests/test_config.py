from config import Settings, get_settings
from database.session import database_url


def test_database_url_alias_and_secret_are_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    assert get_settings().database_url is None

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
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://local@localhost/demo\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    assert Settings(_env_file=env_file).database_url.get_secret_value() == (
        "postgresql://local@localhost/demo"
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://process@localhost/demo")
    assert Settings(_env_file=env_file).database_url.get_secret_value() == (
        "postgresql://process@localhost/demo"
    )
