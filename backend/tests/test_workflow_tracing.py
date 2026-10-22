"""Mutation and background-work traces explain outcomes without extra DB calls."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.documents import Document

from constants import TIMEZONE
from database.models import Order, Rider
from service import alerts, ingestion, preferences, scenarios, setting_changes
from service.preferences import PreferenceService
from service.setting_changes import SettingChanges

AS_OF = datetime(2026, 10, 9, 20, 30, tzinfo=TIMEZONE)


@dataclass
class RecordedWorkflow:
    name: str
    fields: dict[str, Any]
    parent: str | None

    def update(self, **values):
        metadata = self.fields.get("metadata", {}) | values.get("metadata", {})
        self.fields.update(deepcopy(values))
        self.fields["metadata"] = metadata


@dataclass
class Workflows:
    records: list[RecordedWorkflow] = field(default_factory=list)
    active: list[str] = field(default_factory=list)

    @contextmanager
    def observation(self, name, **fields):
        record = RecordedWorkflow(
            name,
            deepcopy(fields),
            self.active[-1] if self.active else None,
        )
        self.records.append(record)
        self.active.append(name)
        try:
            yield record
        except Exception as exc:
            record.update(level="ERROR", status_message=type(exc).__name__)
            raise
        finally:
            self.active.pop()

    def named(self, name: str) -> RecordedWorkflow:
        return next(record for record in reversed(self.records) if record.name == name)


@pytest.fixture
def workflows(monkeypatch):
    state = Workflows()
    for module in (setting_changes, preferences, scenarios, ingestion, alerts):
        monkeypatch.setattr(module, "observation", state.observation)
    monkeypatch.setattr(
        alerts,
        "get_current_span",
        lambda: SimpleNamespace(
            get_span_context=lambda: SimpleNamespace(is_valid=bool(state.active)),
        ),
    )
    return state


def test_confirmed_settings_capture_manager_filter_and_saved_outcome(
    workflows,
    preference_store,
    monkeypatch,
):
    service = PreferenceService("DS-1", "karthik")
    change = SettingChanges(service).propose(
        {"code": "rider_shortage_alert", "value": 1.5},
    )
    other_manager = replace(change, manager_id="ananya")
    monkeypatch.setattr(
        setting_changes,
        "manager_preferences",
        lambda manager_id: service,
    )
    messages = setting_changes.confirm_proposals(
        [change.to_state(), other_manager.to_state()],
        "karthik",
    )
    assert messages[0].startswith("Saved.")
    assert messages[1].startswith("Not saved:")
    assert service.current("rider_shortage_alert").value == 1.5
    confirmed = workflows.named("settings.confirm")
    assert confirmed.fields["user_id"] == "karthik"
    assert confirmed.fields["metadata"]["store_id"] == "DS-1"
    assert confirmed.fields["output"]["skipped_manager_mismatch"] == 1
    assert confirmed.fields["output"]["not_saved"] == 1
    saved = workflows.named("settings.set")
    assert saved.parent == "settings.confirm"
    assert saved.fields["input"]["value"] == 1.5
    assert saved.fields["output"]["outcome"] == "saved"
    assert "above 2 " in saved.fields["output"]["before"]
    assert "above 1.5 " in saved.fields["output"]["after"]


def test_stale_confirmation_is_reported_without_another_write(
    workflows,
    preference_store,
    monkeypatch,
):
    service = PreferenceService("DS-1", "karthik")
    change = SettingChanges(service).propose(
        {"code": "rider_shortage_alert", "value": 1.5},
    )
    service.set("rider_shortage_alert", True, 1)
    workflows.records.clear()
    monkeypatch.setattr(
        setting_changes,
        "manager_preferences",
        lambda manager_id: service,
    )
    messages = setting_changes.confirm_proposals([change.to_state()], "karthik")
    assert "changed after this was proposed" in messages[0]
    assert workflows.named("settings.confirm").fields["output"]["not_saved"] == 1
    assert not any(record.name == "settings.set" for record in workflows.records)
    assert len(preference_store.rows) == 1


def test_settings_batch_and_resets_report_partial_save_rejection_and_noop(
    workflows,
    preference_store,
):
    service = PreferenceService("DS-1", "karthik")
    messages = service.save(
        [
            {"code": "rider_shortage_alert", "enabled": True, "value": 3},
            {"code": "sla_dip_alert", "enabled": True, "value": 85},
            {"code": "sla_dip_alert", "enabled": True, "value": 85},
        ],
    )
    assert len(messages) == 2
    saved = workflows.named("settings.save")
    assert [item["outcome"] for item in saved.fields["output"]["results"]] == [
        "rejected",
        "saved",
        "unchanged",
    ]
    assert "must be" in saved.fields["output"]["results"][0]["reason"]
    assert service.reset("sla_dip_alert").startswith("Reset")
    assert workflows.named("settings.reset").fields["output"]["outcome"] == "reset"
    assert "already" in service.reset("sla_dip_alert")
    assert workflows.named("settings.reset").fields["output"]["outcome"] == "unchanged"


def test_setting_write_failure_is_traced_once_and_still_raises_original_error(
    workflows,
    preference_store,
    monkeypatch,
):
    error = RuntimeError("database failed")
    calls = []

    def fail(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(preferences, "save_preference", fail)
    with pytest.raises(RuntimeError) as caught:
        PreferenceService("DS-1", "karthik").set("rider_shortage_alert", True, 1.5)
    assert caught.value is error
    assert len(calls) == 1
    failed = workflows.named("settings.set")
    assert failed.fields["level"] == "ERROR"
    assert failed.fields["status_message"] == "RuntimeError"


def scenario_store(monkeypatch, rows=None):
    saved = list(rows or [])

    def replace_rows(rows, engine=None):
        saved[:] = rows

    monkeypatch.setattr(scenarios, "replace_scenario", replace_rows)
    monkeypatch.setattr(scenarios, "read_scenario_rows", lambda engine=None: saved)
    return saved


def test_scenario_load_and_reload_capture_committed_snapshot_and_fresh_clock(
    workflows,
    monkeypatch,
):
    rows, _ = scenarios.build_scenario("rain", AS_OF)
    scenario_store(monkeypatch, rows)
    loaded = scenarios.reload_current_scenario()
    reset = workflows.named("scenario.load")
    assert reset.parent == "scenario.reload"
    assert reset.fields["metadata"]["reset_committed"] is True
    assert reset.fields["metadata"]["metrics_cache_invalidated"] is True
    assert reset.fields["metadata"]["store_id"] == loaded["store_id"]
    assert reset.fields["input"]["prepared_snapshot"]["counts"]["orders"] == 12
    assert reset.fields["output"]["snapshot"] == loaded
    reload = workflows.named("scenario.reload")
    assert reload.fields["input"]["previous_as_of"] == AS_OF.isoformat()
    assert reload.fields["output"]["as_of"] == loaded["as_of"]
    assert reload.fields["output"]["counts"] == loaded["counts"]


@pytest.mark.parametrize("stage", ["write", "read_after_commit"])
def test_scenario_failure_distinguishes_rollback_from_committed_reset(
    workflows,
    monkeypatch,
    stage,
):
    error = RuntimeError("database unavailable")
    calls = []

    def replace_rows(rows, engine=None):
        calls.append("write")
        if stage == "write":
            raise error

    def read(engine=None):
        calls.append("read")
        raise error

    monkeypatch.setattr(scenarios, "replace_scenario", replace_rows)
    monkeypatch.setattr(scenarios, "current_scenario", read)
    with pytest.raises(RuntimeError) as caught:
        scenarios.load_scenario("normal")
    assert caught.value is error
    failed = workflows.named("scenario.load")
    assert failed.fields["level"] == "ERROR"
    assert failed.fields["metadata"]["reset_committed"] is (stage != "write")
    assert calls == (["write"] if stage == "write" else ["write", "read"])


def test_ingestion_records_chunks_source_ids_and_persistence_count(
    workflows,
    monkeypatch,
):
    documents = [
        Document(
            page_content="A grounded policy passage.",
            metadata={
                "source": "corpus/policy.pdf",
                "doc_id": "DD-TEST-001",
                "file_hash": "ab" * 32,
                "version": "1.0",
                "title": "Test policy",
            },
        ),
    ]
    ids = ["DD-TEST-001#safety"]
    corpus = SimpleNamespace(load=lambda: (documents, ids))
    embedding = SimpleNamespace(
        model="fake-embedding",
        embed_documents=lambda texts: [[0.1]],
    )
    stored = []
    monkeypatch.setattr(
        ingestion,
        "insert_chunks",
        lambda documents: stored.extend(documents) or 1,
    )
    assert ingestion.IngestionService(corpus, embedding).run() == 1
    parsed = workflows.named("ingestion.corpus")
    assert parsed.parent == "ingestion.run"
    assert parsed.fields["output"]["chunk_ids"] == ids
    assert parsed.fields["output"]["chunks"][0]["content"] == documents[0].page_content
    persisted = workflows.named("ingestion.persist")
    assert persisted.fields["input"]["documents"][0]["file_hash"] == "ab" * 32
    assert (
        persisted.fields["input"]["documents"][0]["metadata"]["doc_id"] == "DD-TEST-001"
    )
    assert persisted.fields["input"]["documents"][0]["stored_chunk_ids"] == [
        "DD-TEST-001#0",
    ]
    assert persisted.fields["output"] == {"stored_chunks": 1}
    assert workflows.named("ingestion.run").fields["output"]["stored_chunks"] == 1
    assert len(stored) == 1


@pytest.mark.parametrize("stage", ["corpus", "persist"])
def test_ingestion_stage_failure_does_not_retry_writes(workflows, monkeypatch, stage):
    error = RuntimeError("ingestion stage failed")
    calls = []
    document = Document(
        page_content="Policy.",
        metadata={
            "source": "policy.pdf",
            "doc_id": "DD-TEST-001",
            "file_hash": "ab" * 32,
            "version": "1.0",
            "title": "Policy",
        },
    )

    def load():
        if stage == "corpus":
            raise error
        return [document], ["DD-TEST-001#policy"]

    def write(documents):
        calls.append("write")
        raise error

    monkeypatch.setattr(ingestion, "insert_chunks", write)
    corpus = SimpleNamespace(load=load)
    embedding = SimpleNamespace(model="fake", embed_documents=lambda texts: [[0.1]])
    with pytest.raises(RuntimeError) as caught:
        ingestion.IngestionService(corpus, embedding).run()
    assert caught.value is error
    assert calls == ([] if stage == "corpus" else ["write"])
    assert workflows.named(f"ingestion.{stage}").fields["level"] == "ERROR"
    assert workflows.named("ingestion.run").fields["level"] == "ERROR"


def alert_store(monkeypatch):
    rows, _ = scenarios.build_scenario("backlog", AS_OF)
    snapshot = alerts.Snapshot(
        AS_OF,
        [row for row in rows if isinstance(row, Order)],
        [row for row in rows if isinstance(row, Rider)],
        [],
    )
    events = []

    def record(values, cooldown_min, engine=None):
        if any(
            event.code == values["code"] and event.manager_id == values["manager_id"]
            for event in events
        ):
            return None
        event = SimpleNamespace(
            id=uuid4(),
            triggered_at=AS_OF,
            dismissed_at=None,
            **values,
        )
        events.append(event)
        return event

    monkeypatch.setattr(alerts, "live_snapshot", lambda engine=None: snapshot)
    monkeypatch.setattr(
        alerts,
        "manager_preferences",
        lambda manager_id: PreferenceService("DS-1", manager_id),
    )
    monkeypatch.setattr(
        alerts,
        "get_settings",
        lambda: SimpleNamespace(alert_cooldown_override_min=None),
    )
    monkeypatch.setattr(alerts, "record_trigger", record)
    monkeypatch.setattr(
        alerts,
        "triggers_since",
        lambda store_id, manager_id, *args, **kwargs: [
            event for event in events if event.manager_id == manager_id
        ],
    )
    return events


def test_standalone_alert_poll_traces_new_events_but_not_repeated_cooldown_reads(
    workflows,
    preference_store,
    monkeypatch,
):
    events = alert_store(monkeypatch)
    first = alerts.check_alerts("karthik", "DS-1", AS_OF)
    assert first.recorded == len(events) == 2
    event_trace = workflows.named("alerts.recorded")
    assert event_trace.fields["user_id"] == "karthik"
    assert event_trace.fields["output"]["recorded"] == 2
    assert len(event_trace.fields["output"]["recent_event_ids"]) == 2
    assert all(
        item["outcome"] == "recorded"
        for item in event_trace.fields["output"]["decisions"]
        if item["breached"]
    )
    workflows.records.clear()
    assert alerts.check_alerts("karthik", "DS-1", AS_OF).recorded == 0
    assert workflows.records == []
    with workflows.observation("job.alerts"):
        assert alerts.check_alerts("karthik", "DS-1", AS_OF).recorded == 0
    checked = workflows.named("alerts.check_manager")
    assert checked.parent == "job.alerts"
    assert all(
        item["outcome"] == "cooldown_suppressed"
        for item in checked.fields["output"]["decisions"]
        if item["breached"]
    )
    assert checked.fields["input"]["snapshot_as_of"] == AS_OF.isoformat()


def test_unparented_alert_poll_error_is_traced_and_not_retried(workflows, monkeypatch):
    error = RuntimeError("snapshot unavailable")
    calls = []

    def fail(engine):
        calls.append("read")
        raise error

    monkeypatch.setattr(alerts, "live_snapshot", fail)
    with pytest.raises(RuntimeError) as caught:
        alerts.check_alerts("karthik", "DS-1", AS_OF)
    assert caught.value is error
    assert calls == ["read"]
    failed = workflows.named("alerts.check_failed")
    assert failed.fields["level"] == "ERROR"
    assert failed.fields["input"]["manager_id"] == "karthik"
