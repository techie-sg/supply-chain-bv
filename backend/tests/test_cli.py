import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cli
import logging_config
from service import summaries
from ui import gradio_app


@pytest.mark.parametrize(
    "arguments, code",
    [(["--help"], 0), (["unknown"], 2), ([], 2)],
)
def test_cli_usage_exits_without_starting_services(tmp_path, arguments, code) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(cli.__file__).resolve()), *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == code
    assert "Traceback" not in result.stderr
    if code == 0:
        assert all(
            name in result.stdout for name in ("app", "summaries", "migrate", "ingest")
        )


def test_app_launch_never_runs_summarization(monkeypatch) -> None:
    summary_service = Mock()
    launch = Mock()
    monkeypatch.setattr(summaries, "summary_service", summary_service)
    monkeypatch.setattr(gradio_app, "configure_logging", lambda: None)
    monkeypatch.setattr(gradio_app.app, "launch", launch)
    cli.main(["app"])
    summary_service.assert_not_called()
    launch.assert_called_once()


def test_summary_command_runs_one_batch_and_returns(monkeypatch) -> None:
    service = Mock()
    service.summarize_idle.return_value = 2
    monkeypatch.setattr(summaries, "summary_service", lambda: service)
    monkeypatch.setattr(logging_config, "configure_logging", lambda: None)
    cli.main(["summaries"])
    service.summarize_idle.assert_called_once_with()


def test_summary_startup_failure_propagates_to_fail_the_cron_run(monkeypatch) -> None:
    service = Mock()
    service.summarize_idle.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(summaries, "summary_service", lambda: service)
    monkeypatch.setattr(logging_config, "configure_logging", lambda: None)
    with pytest.raises(RuntimeError, match="database unavailable"):
        cli.main(["summaries"])


def test_migration_resolves_config_even_outside_backend(monkeypatch, tmp_path) -> None:
    from alembic import command

    upgrade = Mock()
    monkeypatch.setattr(command, "upgrade", upgrade)
    monkeypatch.chdir(tmp_path)
    cli.main(["migrate"])
    config, revision = upgrade.call_args.args
    assert Path(config.config_file_name) == Path(cli.__file__).with_name("alembic.ini")
    assert Path(config.get_main_option("script_location")).is_dir()
    assert revision == "head"


def test_ingestion_is_explicit_and_runs_once(monkeypatch) -> None:
    ingest = Mock()
    monkeypatch.setitem(sys.modules, "service.ingestion", SimpleNamespace(main=ingest))
    cli.main(["ingest"])
    ingest.assert_called_once_with()
