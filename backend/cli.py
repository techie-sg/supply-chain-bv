"""Central entry point: uv run python cli.py <command>."""

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run DispatchDesk services and jobs.")
    commands = parser.add_subparsers(dest="command", required=True)
    app = commands.add_parser("app", help="Start the Gradio application")
    app.add_argument(
        "--no-scheduler",
        action="store_true",
        help="Disable the internal scheduled jobs (idle summaries, daily review) "
        "when using external cron",
    )
    commands.add_parser("summaries", help="Summarize idle conversations once and exit")
    commands.add_parser("review", help="Run the daily review (dreaming) once and exit")
    commands.add_parser("migrate", help="Apply database migrations up to head")
    commands.add_parser("ingest", help="Ingest the configured policy corpus")
    args = parser.parse_args(argv)

    # Import only the selected command's dependencies. Cron needs no Gradio UI.
    if args.command == "app":
        from ui.gradio_app import main as run_app

        run_app(run_scheduler=not args.no_scheduler)
    elif args.command == "summaries":
        import structlog

        from logging_config import configure_logging
        from service.summaries import summary_service

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
