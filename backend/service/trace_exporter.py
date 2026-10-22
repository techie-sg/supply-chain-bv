"""Account for accepted and failed trace exports without affecting application work."""

from collections.abc import Sequence
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import structlog
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = structlog.stdlib.get_logger(__name__)


class ReportingSpanExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter):
        self._exporter = exporter
        self._lock = Lock()
        self._accepted = 0
        self._failed = 0
        self._last_export_at: str | None = None
        self._last_result: str | None = None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        error_type = None
        try:
            result = self._exporter.export(spans)
        except Exception as exc:  # noqa: BLE001 - tracing cannot fail application work
            result = SpanExportResult.FAILURE
            error_type = type(exc).__name__
        accepted = result == SpanExportResult.SUCCESS
        with self._lock:
            self._accepted += len(spans) if accepted else 0
            self._failed += 0 if accepted else len(spans)
            self._last_export_at = datetime.now(UTC).isoformat()
            self._last_result = "accepted" if accepted else "failed"
        if not accepted:
            logger.warning(
                "Langfuse export failed",
                spans=len(spans),
                error_type=error_type,
            )
        return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "accepted_spans": self._accepted,
                "failed_spans": self._failed,
                "last_export_at": self._last_export_at,
                "last_export_result": self._last_result,
            }

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._exporter.force_flush(timeout_millis)
