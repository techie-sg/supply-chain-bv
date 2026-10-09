"""Preferences are editable, scoped and unavailable storage cannot be overwritten."""

from uuid import uuid4

import gradio as gr
import pytest

from service.personalization import item
from ui import personalization


def test_load_values_and_source_link_preserve_manager(monkeypatch):
    profile = {
        "answer_length": item(
            "brief",
            "chat",
            conversation_id=str(uuid4()),
            quote="Always keep answers short.",
        ),
    }

    class Service:
        def profile(self):
            return profile

    monkeypatch.setattr(
        personalization,
        "manager_personalization",
        lambda manager_id: Service(),
    )
    values = personalization.load("ananya")
    assert "Answer length: Brief" in values[0]
    assert values[-3] == {"manager_id": "ananya", "profile": profile}
    assert "manager=ananya" in values[-2] and "View conversation" in values[-2]


def test_unavailable_storage_invalidates_edit_snapshot(monkeypatch):
    def unavailable(manager_id):
        raise RuntimeError("No database")

    monkeypatch.setattr(personalization, "manager_personalization", unavailable)
    values = personalization.load("karthik")
    assert values[-3] is None and "unavailable" in values[-1]
    with pytest.raises(gr.Error, match="Refresh"):
        personalization.save("karthik", None)
    with pytest.raises(gr.Error, match="Refresh"):
        personalization.save("karthik", {"manager_id": "ananya", "profile": {}})


def test_dreaming_source_is_visible_with_evidence_and_chat_link():
    profile = {
        "answer_length": item(
            "brief",
            "dreaming",
            conversation_id=str(uuid4()),
            quote="Keep answers short for me",
        ),
    }
    values = personalization._values(profile, "ananya")
    assert "learned during conversation review" in values[-2]
    assert "Keep answers short for me" in values[-2]
    assert "View conversation" in values[-2] and "manager=ananya" in values[-2]


def test_settings_show_personalization_controls_and_no_memory_digest(ui_app):
    ids = {
        component.elem_id
        for component in ui_app.blocks.values()
        if hasattr(component, "elem_id")
    }
    assert (
        "personalization-answer_length" not in ids
        and "personalization-instructions" in ids
    )
    assert "memory-digest" not in ids
