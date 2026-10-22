"""Optional Langfuse observations that never change application behavior."""

# Every SDK boundary is best effort, including unexpected SDK exceptions.
# ruff: noqa: BLE001

import atexit
import base64
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
from enum import Enum
from functools import lru_cache, wraps
from importlib.metadata import version
from typing import Any, Literal, ParamSpec, TypeVar
from uuid import UUID

import structlog
from langfuse import Langfuse, propagate_attributes
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from config import Settings, get_settings
from service.trace_exporter import ReportingSpanExporter

logger = structlog.stdlib.get_logger(__name__)

ObservationKind = Literal[
    "span",
    "agent",
    "chain",
    "generation",
    "embedding",
    "retriever",
    "tool",
    "guardrail",
]
_SECRET_FIELDS = {
    "authorization",
    "api_key",
    "apikey",
    "secret_key",
    "password",
    "database_url",
    "db_url",
    "openrouter_key",
    "open_router_key",
    "access_token",
    "refresh_token",
}
_CORRELATION_FIELDS = {
    "store_id",
    "request_id",
    "conversation_id",
    "manager_id",
    "command",
    "diagnostic",
    "component",
    "purpose",
}


def _redact(value: Any, active: set[int] | None = None) -> Any:
    """Serialize explicit trace fields, never provider clients or settings objects."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _redact(value.value, active)
    if isinstance(value, (Mapping, list, tuple)):
        active = active if active is not None else set()
        identity = id(value)
        if identity in active:
            return "[circular reference]"
        active.add(identity)
        try:
            if isinstance(value, Mapping):
                result = {}
                for key, item in value.items():
                    name = str(key).lower().replace("-", "_")
                    secret = name in _SECRET_FIELDS or name.endswith(
                        ("_api_key", "_secret_key", "_password"),
                    )
                    result[str(key)] = "[redacted]" if secret else _redact(item, active)
                return result
            return [_redact(item, active) for item in value]
        finally:
            active.remove(identity)
    return f"[{type(value).__name__}]"


def _propagation_metadata(values: dict[str, Any]) -> dict[str, Any]:
    # Langfuse propagation dimensions have a 200-character limit. Larger
    # metadata remains on its observation; do not silently propagate a preview.
    return {
        key: value
        for key, value in values.items()
        if key in _CORRELATION_FIELDS
        and value is not None
        and len(json.dumps(value)) <= 200
    }


def _tracing_failure(operation: str, exc: Exception) -> None:
    # Arbitrary exception text may contain provider payloads or credentials.
    logger.warning(
        "Langfuse tracing unavailable",
        operation=operation,
        error_type=type(exc).__name__,
    )


class Observation:
    """A safe update facade; a missing SDK span simply discards updates."""

    def __init__(self, span: Any = None, *, capture_content: bool = True):
        self._span = span
        self._capture_content = capture_content
        self._metadata: dict[str, Any] = {}

    def fields(self, values: dict[str, Any]) -> dict[str, Any]:
        fields = dict(values)
        for name in ("input", "output"):
            if not self._capture_content:
                fields.pop(name, None)
            elif name in fields:
                fields[name] = _redact(fields[name])
        if "metadata" in fields:
            self._metadata.update(_redact(fields["metadata"]) or {})
            # OTel defaults to 128 attributes; Langfuse flattens metadata keys.
            # Pack large maps into one JSON value instead of losing later keys.
            fields["metadata"] = (
                {
                    "details": dict(self._metadata),
                    **_propagation_metadata(self._metadata),
                }
                if len(self._metadata) > 48
                else dict(self._metadata)
            )
        return fields

    def update(self, **values: Any) -> None:
        if self._span is None:
            return
        try:
            self._span.update(**self.fields(values))
        except Exception as exc:
            _tracing_failure("update", exc)

    def trace_context(self) -> dict[str, str] | None:
        try:
            if self._span is None:
                return None
            trace_id = getattr(self._span, "trace_id", None)
            span_id = getattr(self._span, "id", None)
            if isinstance(trace_id, str) and isinstance(span_id, str):
                return {"trace_id": trace_id, "parent_span_id": span_id}
        except Exception as exc:
            _tracing_failure("context", exc)
        return None


class LangfuseService:
    def __init__(self, settings: Settings | None = None):
        self._client: Any = None
        self._exporter: ReportingSpanExporter | None = None
        self.capture_content = True
        try:
            settings = settings or get_settings()
            self.capture_content = settings.langfuse_capture_content
            public = settings.langfuse_public_key
            secret = settings.langfuse_secret_key
            if (
                not settings.langfuse_tracing_enabled
                or not public
                or not public.strip()
                or not secret
                or not secret.get_secret_value().strip()
            ):
                return
            credentials = base64.b64encode(
                f"{public.strip()}:{secret.get_secret_value().strip()}".encode(),
            ).decode("ascii")
            self._exporter = ReportingSpanExporter(
                OTLPSpanExporter(
                    endpoint=f"{settings.langfuse_base_url.rstrip('/')}/api/public/otel/v1/traces",
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "x-langfuse-sdk-name": "python",
                        "x-langfuse-sdk-version": version("langfuse"),
                        "x-langfuse-public-key": public.strip(),
                        "x-langfuse-ingestion-version": "4",
                    },
                    timeout=5,
                    compression=Compression.Gzip,
                ),
            )
            self._client = Langfuse(
                public_key=public.strip(),
                secret_key=secret.get_secret_value().strip(),
                base_url=settings.langfuse_base_url,
                environment=settings.langfuse_environment,
                timeout=5,
                span_exporter=self._exporter,
            )
        except Exception as exc:
            _tracing_failure("initialize", exc)
            if self._exporter is not None:
                try:
                    self._exporter.shutdown()
                except Exception as close_error:
                    _tracing_failure("exporter-shutdown", close_error)

    @contextmanager
    def observation(
        self,
        name: str,
        *,
        kind: ObservationKind = "span",
        user_id: str | None = None,
        session_id: str | None = None,
        **values: Any,
    ) -> Iterator[Observation]:
        if self._client is None:
            yield Observation()
            return
        stack = ExitStack()
        observation = Observation(capture_content=self.capture_content)
        try:
            fields = observation.fields(values)
            metadata = dict(fields.get("metadata") or {})
            if request_id := structlog.contextvars.get_contextvars().get("request_id"):
                metadata["request_id"] = request_id
            stack.enter_context(
                propagate_attributes(
                    user_id=user_id,
                    session_id=session_id,
                    metadata=_propagation_metadata(metadata),
                ),
            )
            observation._span = stack.enter_context(
                self._client.start_as_current_observation(
                    name=name,
                    as_type=kind,
                    **fields,
                ),
            )
        except Exception as exc:
            _tracing_failure("start", exc)
            self._close(stack)
            yield Observation()
            return
        try:
            yield observation
        except BaseException as exc:
            observation.update(level="ERROR", status_message=type(exc).__name__)
            raise
        finally:
            # Closing without the application's exception prevents the SDK from
            # exporting an arbitrary provider error body or swallowing the error.
            self._close(stack)

    @staticmethod
    def _close(stack: ExitStack) -> None:
        try:
            stack.close()
        except Exception as exc:
            _tracing_failure("end", exc)

    def shutdown(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.shutdown()
            except Exception as exc:
                _tracing_failure("shutdown", exc)

    def export_status(self) -> dict[str, Any]:
        return {
            "enabled": self._client is not None,
            **(self._exporter.status() if self._exporter else {}),
        }


@lru_cache(maxsize=1)
def get_tracing_service() -> LangfuseService:
    return LangfuseService()


@contextmanager
def observation(name: str, **values: Any) -> Iterator[Observation]:
    with get_tracing_service().observation(name, **values) as span:
        yield span


@contextmanager
def trace_attributes(**values: Any) -> Iterator[None]:
    """Associate a resolved chat with the current observation and its children."""
    if get_tracing_service()._client is None:
        yield
        return
    stack = ExitStack()
    try:
        if "metadata" in values:
            values["metadata"] = _propagation_metadata(_redact(values["metadata"]))
        stack.enter_context(propagate_attributes(**values))
    except Exception as exc:
        _tracing_failure("associate", exc)
    try:
        yield
    finally:
        LangfuseService._close(stack)


P = ParamSpec("P")
R = TypeVar("R")


def traced(
    name: str,
    *,
    kind: ObservationKind = "span",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Label a workflow without automatically capturing its arguments."""

    def decorate(call: Callable[P, R]) -> Callable[P, R]:
        @wraps(call)
        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            with observation(name, kind=kind):
                return call(*args, **kwargs)

        return run

    return decorate


def shutdown_tracing() -> None:
    if get_tracing_service.cache_info().currsize:
        get_tracing_service().shutdown()
        get_tracing_service.cache_clear()


atexit.register(shutdown_tracing)
