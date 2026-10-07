"""A minimal in-process interval job, started with the app.

Each run is isolated: an error is logged and the next run still happens. Saves
made by jobs must be safe to repeat, since two app instances may run the same job.
"""

import threading
from collections.abc import Callable

import structlog

logger = structlog.stdlib.get_logger(__name__)


class IntervalJob:
    def __init__(
        self,
        name: str,
        interval_seconds: float,
        run: Callable[[], object],
    ) -> None:
        self.name = name
        self.interval_seconds = interval_seconds
        self.run = run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> None:
        try:
            result = self.run()
        except Exception:
            logger.exception("Scheduled job failed", job=self.name)
        else:
            logger.debug("Scheduled job ran", job=self.name, result=result)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.run_once()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()
        logger.info(
            "Scheduled job started",
            job=self.name,
            every_s=self.interval_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
