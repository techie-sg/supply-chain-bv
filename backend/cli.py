"""Central entry point: uv run python cli.py <command>."""

import argparse
from pathlib import Path

import structlog

from logging_config import configure_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run DispatchDesk services and jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("app", help="Start the Gradio application")
    commands.add_parser("summaries", help="Summarize idle conversations once and exit")
    commands.add_parser("review", help="Run the daily review (dreaming) once and exit")
    commands.add_parser("migrate", help="Apply database migrations up to head")
    commands.add_parser("ingest", help="Ingest the configured policy corpus")
    args = parser.parse_args(argv)
    # Configure before importing Gradio, model providers, or database services.
    configure_logging()
    logger = structlog.stdlib.get_logger(__name__)
    logger.info("DispatchDesk command starting", command=args.command)
    try:
        run_command(args)
    except Exception:
        logger.exception("DispatchDesk command failed", command=args.command)
        raise SystemExit(1) from None


def run_command(args: argparse.Namespace) -> None:
    # Import only the selected command's dependencies. Cron needs no Gradio UI.
    if args.command == "app":
        from ui.gradio_app import main as run_app

        run_app()
    elif args.command == "summaries":
        from service.summaries import summary_service

        # Pick up console handlers installed during provider imports, too.
        configure_logging()
        count = summary_service().summarize_idle()
        structlog.stdlib.get_logger(__name__).info(
            "Idle conversation summary job completed",
            summarized=count,
        )
    elif args.command == "review":
        import structlog

        from logging_config import configure_logging
        from service.dreaming import run_review

        configure_logging()
        report = run_review()
        structlog.stdlib.get_logger(__name__).info(
            "Daily review job completed",
            report=report.text(),
        )
    elif args.command == "migrate":
        from alembic.config import Config

        from alembic import command

        config = Config(str(Path(__file__).resolve().with_name("alembic.ini")))
        command.upgrade(config, "head")
    else:
        from service.ingestion import main as ingest

        ingest()


if __name__ == "__main__":
    main()
