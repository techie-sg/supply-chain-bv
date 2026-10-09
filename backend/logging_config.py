"""Shared structlog setup for application and standard-library logs."""

import logging
import sys

import structlog

from config import get_settings


def configure_logging() -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.EventRenamer("message"),
                structlog.processors.JSONRenderer(),
            ],
        ),
    )
    logging.basicConfig(level=get_settings().log_level, handlers=[handler], force=True)
    # Library-owned console handlers bypass the root JSON/stdout handler.
    for candidate in list(logging.Logger.manager.loggerDict.values()):
        if not isinstance(candidate, logging.Logger):
            continue
        for existing in candidate.handlers[:]:
            if isinstance(existing, logging.StreamHandler) and not isinstance(
                existing,
                logging.FileHandler,
            ):
                candidate.removeHandler(existing)
                existing.close()
                candidate.propagate = True
    logging.captureWarnings(True)
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_uvicorn_logging() -> None:
    """Keep Gradio's server startup from reinstalling stderr handlers."""
    from uvicorn.config import LOGGING_CONFIG

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        LOGGING_CONFIG["loggers"][name] = {
            "handlers": [],
            "level": "NOTSET",
            "propagate": True,
        }
