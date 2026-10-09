import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cli
from service import summaries
from ui import gradio_app


@pytest.fixture(autouse=True)
def isolate_logging(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(gradio_app, "configure_uvicorn_logging", lambda: None)


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
    cli.main(["summaries"])
    service.summarize_idle.assert_called_once_with()


def test_summary_startup_failure_propagates_to_fail_the_cron_run(monkeypatch) -> None:
    service = Mock()
    service.summarize_idle.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(summaries, "summary_service", lambda: service)
    with pytest.raises(SystemExit) as error:
        cli.main(["summaries"])
    assert error.value.code == 1


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


def test_review_command_runs_the_daily_review_once(monkeypatch) -> None:
    from service import dreaming

    report = dreaming.ReviewReport(chats=2, handover_drafts=1)
    calls: list[bool] = []

    def run_review():
        calls.append(True)
        return report

    monkeypatch.setattr(dreaming, "run_review", run_review)
    cli.main(["review"])
    assert calls == [True]


def test_review_failure_propagates_to_fail_the_cron_run(monkeypatch) -> None:
    from service import dreaming

    def failing():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(dreaming, "run_review", failing)
    with pytest.raises(SystemExit) as error:
        cli.main(["review"])
    assert error.value.code == 1


def test_alerts_command_checks_every_manager_once_by_default(monkeypatch) -> None:
    from service import alerts

    calls: list[bool] = []

    def check_store():
        calls.append(True)
        return alerts.StoreCheck(available=True, recorded={"karthik": 1})

    monkeypatch.setattr(alerts, "check_store", check_store)
    cli.main(["alerts"])
    assert calls == [True]


def test_alerts_command_repeats_on_the_interval_and_stops(monkeypatch) -> None:
    from service import alerts

    calls: list[bool] = []

    def check_store():
        calls.append(True)
        return alerts.StoreCheck(True, {})

    monkeypatch.setattr(alerts, "check_store", check_store)
    waits: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", waits.append)
    cli.main(["alerts", "--runs", "3", "--every", "60"])
    assert len(calls) == 3
    # Two waits between three checks, each just under a minute.
    assert len(waits) == 2 and all(55 < wait <= 60 for wait in waits)


def test_run_alert_checks_forever_mode_and_bad_values() -> None:
    runs: list[int] = []

    class Stop(Exception):
        pass

    def check():
        runs.append(1)
        if len(runs) == 4:
            raise Stop

    with pytest.raises(Stop):
        cli.run_alert_checks(check, 0, 60, sleep=lambda seconds: None)
    assert len(runs) == 4
    assert cli.run_alert_checks(lambda: None, 2, 0, sleep=lambda seconds: None) == 2
    with pytest.raises(ValueError, match="must not be negative"):
        cli.run_alert_checks(lambda: None, -1, 60)


def test_alerts_command_failure_fails_the_cron_run(monkeypatch) -> None:
    from service import alerts

    def failing():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(alerts, "check_store", failing)
    with pytest.raises(SystemExit) as error:
        cli.main(["alerts"])
    assert error.value.code == 1
