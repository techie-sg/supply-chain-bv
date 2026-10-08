"""Exercise real console streams without replacing pytest's logging handlers."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def run_logging_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from types import SimpleNamespace\n"
            "import logging_config\n"
            "logging_config.get_settings = lambda: SimpleNamespace(log_level='INFO')\n"
            + script,
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_library_handlers_and_reconfiguration_preserve_levels_on_stdout():
    result = run_logging_script(
        "import logging, sys, warnings, structlog\n"
        "library = logging.getLogger('library')\n"
        "library.addHandler(logging.StreamHandler(sys.stderr))\n"
        "library.propagate = False\n"
        "logging_config.configure_logging()\n"
        "logging_config.configure_logging()\n"
        "library.info('library ready')\n"
        "structlog.get_logger('job').info('job ready', count=2)\n"
        "warnings.warn('check settings')\n"
        "try:\n"
        "    raise RuntimeError('job failed')\n"
        "except RuntimeError:\n"
        "    library.exception('failure')\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [record["level"] for record in records] == [
        "info",
        "info",
        "warning",
        "error",
    ]
    assert records[0]["message"] == "library ready"
    assert records[1]["count"] == 2
    assert "RuntimeError: job failed" in records[-1]["exception"]


def test_gradio_uvicorn_configuration_keeps_json_stdout():
    result = run_logging_script(
        "import logging\n"
        "from uvicorn import Config\n"
        "logging_config.configure_logging()\n"
        "logging_config.configure_uvicorn_logging()\n"
        "Config(app='unused:app', log_level='info')\n"
        "logging.getLogger('uvicorn.error').info('server ready')\n"
        "logging.getLogger('uvicorn.error').error('server failed')\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [(record["level"], record["message"]) for record in records] == [
        ("info", "server ready"),
        ("error", "server failed"),
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_cron_logs_completion_or_failure_without_raw_stderr(fail):
    result = run_logging_script(
        "import sys, cli\n"
        "def summarize_idle():\n"
        + (
            "    raise RuntimeError('database unavailable')\n"
            if fail
            else "    return 2\n"
        )
        + "sys.modules['service.summaries'] = SimpleNamespace(\n"
        "    summary_service=lambda: SimpleNamespace(summarize_idle=summarize_idle))\n"
        "cli.main(['summaries'])\n",
    )
    assert result.returncode == (1 if fail else 0), result.stderr
    assert result.stderr == ""
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]["message"] == "DispatchDesk command starting"
    assert records[0]["level"] == "info"
    last = records[-1]
    if fail:
        assert last["level"] == "error"
        assert last["command"] == "summaries"
        assert "RuntimeError: database unavailable" in last["exception"]
    else:
        assert last["level"] == "info"
        assert last["summarized"] == 2
