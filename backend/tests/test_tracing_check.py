"""The diagnostic verifies persisted full content and hierarchy, without model calls."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from service import tracing_check


@pytest.fixture
def diagnostic(monkeypatch):
    rows = {}
    service = SimpleNamespace(capture_content=True)
    fail = {
        "auth": False,
        "export": False,
        "readback": False,
        "truncate": False,
        "flat_metadata": False,
        "unexpected_content": False,
    }

    @contextmanager
    def observation(name, **kwargs):
        parent = rows.get("dispatchdesk.tracing-check")
        row = SimpleNamespace(
            name=name,
            id="child" if parent else "root",
            trace_id="trace-id",
            parent_observation_id="root" if parent else None,
            session_id=parent.session_id if parent else kwargs.get("session_id"),
            user_id="diagnostic",
            input=json.dumps(kwargs.get("input")) if service.capture_content else None,
            output=None,
            metadata={"details": json.dumps(kwargs.get("metadata", {}))},
        )
        rows[name] = row

        def update(**fields):
            if service.capture_content and "output" in fields:
                row.output = json.dumps(fields["output"])

        yield SimpleNamespace(
            trace_context=lambda: {"trace_id": "trace-id", "parent_span_id": row.id},
            update=update,
        )

    def readback(**kwargs):
        assert kwargs["trace_id"] == "trace-id"
        assert kwargs["expand_metadata"] == "details"
        assert kwargs["request_options"]["max_retries"] == 0
        if fail["truncate"]:
            rows["dispatchdesk.tracing-check.child"].output = "{}"
        if fail["unexpected_content"]:
            rows["dispatchdesk.tracing-check.child"].output = '{"unexpected":true}'
        if fail["flat_metadata"]:
            parent = rows["dispatchdesk.tracing-check"]
            parent.metadata = {
                f"details.{key}": value
                for key, value in json.loads(parent.metadata["details"]).items()
            }
        return SimpleNamespace(data=[] if fail["readback"] else list(rows.values()))

    service._client = SimpleNamespace(
        auth_check=lambda: not fail["auth"],
        flush=lambda: None,
        api=SimpleNamespace(observations=SimpleNamespace(get_many=readback)),
    )
    service.export_status = lambda: {
        "enabled": True,
        "failed_spans": int(fail["export"]),
    }
    monkeypatch.setattr(tracing_check, "get_tracing_service", lambda: service)
    monkeypatch.setattr(tracing_check, "observation", observation)
    monkeypatch.setattr(tracing_check.time, "sleep", lambda _: None)
    return service, fail


def test_verifies_complete_content_and_parentage(diagnostic):
    result = tracing_check.verify_tracing()
    assert result["full_content_verified"] is True
    assert result["content_characters"] > 100_000
    assert result["provider_requests"] == 0
    assert result["stored_observations"] == 2


def test_metadata_only_mode_is_reported_without_claiming_content_verified(diagnostic):
    diagnostic[0].capture_content = False
    result = tracing_check.verify_tracing()
    assert result["full_content_verified"] is False
    assert result["content_characters"] == 0


def test_verifies_flattened_metadata_readback(diagnostic):
    diagnostic[1]["flat_metadata"] = True
    assert tracing_check.verify_tracing()["stored_observations"] == 2


def test_metadata_only_mode_rejects_unexpected_stored_content(diagnostic):
    diagnostic[0].capture_content = False
    diagnostic[1]["unexpected_content"] = True
    with pytest.raises(RuntimeError, match="despite metadata-only"):
        tracing_check.verify_tracing()


@pytest.mark.parametrize("failure", ["auth", "export", "readback", "truncate"])
def test_missing_storage_or_lost_content_fails_the_diagnostic(diagnostic, failure):
    diagnostic[1][failure] = True
    with pytest.raises(RuntimeError):
        tracing_check.verify_tracing()


def test_disabled_tracing_fails_the_diagnostic(diagnostic):
    diagnostic[0]._client = None
    with pytest.raises(RuntimeError, match="Configure and enable"):
        tracing_check.verify_tracing()
