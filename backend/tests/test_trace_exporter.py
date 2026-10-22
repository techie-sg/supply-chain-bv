"""Export results reflect delivery rather than merely configured credentials."""

from collections.abc import Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from service import trace_exporter
from service.trace_exporter import ReportingSpanExporter


class FakeExporter(SpanExporter):
    def __init__(self, *results: SpanExportResult | Exception):
        self.results = list(results)
        self.batches: list[Sequence[ReadableSpan]] = []
        self.flush_timeouts: list[int] = []
        self.shutdowns = 0
        self.flush_result = True

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.batches.append(spans)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def shutdown(self) -> None:
        self.shutdowns += 1

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self.flush_timeouts.append(timeout_millis)
        return self.flush_result


def batch(size: int) -> list[ReadableSpan]:
    return [cast(ReadableSpan, object()) for _ in range(size)]


@pytest.fixture
def warnings(monkeypatch):
    records = []

    def warning(message, **fields):
        records.append({"message": message, **fields})

    monkeypatch.setattr(trace_exporter, "logger", SimpleNamespace(warning=warning))
    return records


def test_status_is_empty_until_an_export_is_attempted(warnings):
    exporter = ReportingSpanExporter(FakeExporter())
    assert exporter.status() == {
        "accepted_spans": 0,
        "failed_spans": 0,
        "last_export_at": None,
        "last_export_result": None,
    }
    assert warnings == []


def test_export_counts_span_delivery_across_success_and_rejection(warnings):
    target = FakeExporter(SpanExportResult.SUCCESS, SpanExportResult.FAILURE)
    exporter = ReportingSpanExporter(target)
    first = batch(3)
    assert exporter.export(first) == SpanExportResult.SUCCESS
    accepted = exporter.status()
    assert accepted["accepted_spans"] == 3
    assert accepted["failed_spans"] == 0
    assert accepted["last_export_result"] == "accepted"
    assert (
        datetime.fromisoformat(accepted["last_export_at"]).utcoffset().total_seconds()
        == 0
    )
    assert target.batches[0] is first
    assert warnings == []
    second = batch(2)
    assert exporter.export(second) == SpanExportResult.FAILURE
    failed = exporter.status()
    assert failed["accepted_spans"] == 3
    assert failed["failed_spans"] == 2
    assert failed["last_export_result"] == "failed"
    assert failed["last_export_at"] >= accepted["last_export_at"]
    assert target.batches[1] is second
    assert warnings == [
        {"message": "Langfuse export failed", "spans": 2, "error_type": None},
    ]
    # Returned status cannot mutate the counters maintained by the exporter.
    failed["accepted_spans"] = 999
    assert exporter.status()["accepted_spans"] == 3


def test_export_exception_becomes_failure_without_leaking_provider_message(warnings):
    target = FakeExporter(
        RuntimeError("private credential or provider response"),
        SpanExportResult.SUCCESS,
    )
    exporter = ReportingSpanExporter(target)
    assert exporter.export(batch(2)) == SpanExportResult.FAILURE
    failed = exporter.status()
    assert failed["accepted_spans"] == 0
    assert failed["failed_spans"] == 2
    assert failed["last_export_result"] == "failed"
    assert warnings == [
        {"message": "Langfuse export failed", "spans": 2, "error_type": "RuntimeError"},
    ]
    assert "private credential" not in repr([warnings, failed])
    assert exporter.export(batch(1)) == SpanExportResult.SUCCESS
    recovered = exporter.status()
    assert recovered["accepted_spans"] == 1
    assert recovered["failed_spans"] == 2
    assert recovered["last_export_result"] == "accepted"


@pytest.mark.parametrize("result", [True, False])
def test_flush_and_shutdown_delegate_to_the_underlying_exporter(warnings, result):
    target = FakeExporter()
    target.flush_result = result
    exporter = ReportingSpanExporter(target)
    assert exporter.force_flush(1234) is result
    assert exporter.force_flush() is result
    assert target.flush_timeouts == [1234, 30_000]
    exporter.shutdown()
    assert target.shutdowns == 1
    assert exporter.status()["accepted_spans"] == 0
    assert exporter.status()["failed_spans"] == 0
    assert warnings == []
