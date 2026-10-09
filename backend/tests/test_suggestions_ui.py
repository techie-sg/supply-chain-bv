from uuid import uuid4

import gradio as gr
import pytest

from database.models import Suggestion
from ui import gradio_app
from ui import navigation as navigation_ui
from ui import suggestions as suggestions_ui


def item(kind: str, payload: dict, chats: int = 3) -> Suggestion:
    return Suggestion(
        id=uuid4(),
        kind=kind,
        payload=payload,
        reason="Frozen orders came up in several chats.",
        evidence=[{"conversation_id": f"c{index}"} for index in range(chats)],
        status="pending",
    )


SETTING = item(
    "setting",
    {
        "code": "frozen_order_waiting_alert",
        "enabled": True,
        "value": 5,
        "options": None,
    },
)
DRAFT = item(
    "handover_draft",
    {
        "shift_id": "4f1c",
        "started_at": "2026-10-08T14:00:00+05:30",
        "note": "- Standby rider came in.",
    },
    1,
)


def test_refresh_lists_pending_suggestions_with_their_details(
    monkeypatch,
    preference_store,
) -> None:
    monkeypatch.setattr(
        suggestions_ui,
        "pending_suggestions",
        lambda manager_id=None: [SETTING, DRAFT],
    )
    # Hidden from managers for now: pending suggestions don't show the entry.
    assert suggestions_ui.refresh()[0] == gr.update(visible=False)
    monkeypatch.setattr(suggestions_ui, "SHOW_SUGGESTIONS", True)
    entry, entry_text, items, detail, note, actions, _ = suggestions_ui.refresh()
    assert entry == gr.update(visible=True) and actions == gr.update(visible=True)
    assert entry_text == '<p class="sidebar-heading">Suggestions · 2</p>'
    assert items["choices"] == [
        (
            (
                "Setting: Frozen order waiting: on, above 5 minutes, at all times, "
                "at most every 10 min"
            ),
            str(SETTING.id),
        ),
        ("Handover note for your shift started 8 Oct, 14:00", str(DRAFT.id)),
    ]
    assert items["value"] == str(SETTING.id)
    assert "Based on 3 chats." in detail and note == gr.update(visible=False, value="")
    detail, note = suggestions_ui.select(str(DRAFT.id))
    assert "Based on 1 chat." in detail
    assert note == gr.update(visible=True, value="- Standby rider came in.")


def test_empty_or_unavailable_suggestions_hide_the_entry(monkeypatch) -> None:
    monkeypatch.setattr(
        suggestions_ui,
        "pending_suggestions",
        lambda manager_id=None: [],
    )
    entry, _, items, detail, *_ = suggestions_ui.refresh()
    assert entry == gr.update(visible=False) and items["choices"] == []
    assert detail == "No suggestions right now."

    def unavailable(manager_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(suggestions_ui, "pending_suggestions", unavailable)
    assert "unavailable" in suggestions_ui.refresh()[3]
    assert "unavailable" in suggestions_ui.select("x")[0]


def test_accept_and_dismiss_report_their_outcome(monkeypatch, preference_store) -> None:
    # The suggestion is one of the selected manager's pending ones.
    monkeypatch.setattr(
        suggestions_ui,
        "pending_suggestions",
        lambda manager_id=None: [SETTING],
    )
    calls = []

    def accept(suggestion_id, store_id, manager_id, note):
        calls.append((suggestion_id, note))
        return "Saved."

    monkeypatch.setattr(suggestions_ui, "accept_suggestion", accept)

    def dismiss(suggestion_id, store_id, manager_id):
        calls.append(suggestion_id)
        return True

    monkeypatch.setattr(suggestions_ui, "dismiss_suggestion", dismiss)
    target = str(SETTING.id)
    assert suggestions_ui.accept(target, "note")[-1] == "Saved."
    assert suggestions_ui.dismiss(target)[-1] == "Dismissed."
    assert suggestions_ui.accept(None, None)[-1] == "Choose a suggestion first."
    assert suggestions_ui.dismiss(None)[-1] == "Choose a suggestion first."

    def rejected(suggestion_id, store_id, manager_id, note):
        raise LookupError("That suggestion is no longer pending.")

    monkeypatch.setattr(suggestions_ui, "accept_suggestion", rejected)
    assert suggestions_ui.accept(target, None)[-1] == (
        "Not applied: That suggestion is no longer pending."
    )

    def unavailable(*args):
        raise RuntimeError("database down")

    monkeypatch.setattr(suggestions_ui, "accept_suggestion", unavailable)
    monkeypatch.setattr(suggestions_ui, "dismiss_suggestion", unavailable)
    with pytest.raises(gr.Error):
        suggestions_ui.accept(target, None)
    with pytest.raises(gr.Error):
        suggestions_ui.dismiss(target)


def test_sidebar_review_opens_the_suggestions_category(ui_app) -> None:
    nav, *panels, save_row = navigation_ui.show_suggestions()
    assert nav["value"] == "suggestions"
    assert [panel["visible"] for panel in panels] == [
        False,
        False,
        False,
        False,
        True,
        False,
    ]
    assert save_row["visible"] is False
    review = next(
        block
        for block in ui_app.blocks.values()
        if isinstance(block, gr.Button) and block.elem_id == "review-suggestions"
    )
    [click] = [
        callback
        for callback in ui_app.fns.values()
        if (review._id, "click") in callback.targets
    ]
    assert click.fn is navigation_ui.open_settings
    [then] = [
        callback
        for callback in ui_app.fns.values()
        if callback.fn is navigation_ui.show_suggestions
    ]
    assert then.trigger_after == click._id
    names = {getattr(callback.fn, "__name__", None) for callback in ui_app.fns.values()}
    assert {
        "refresh_for",
        "accept",
        "dismiss",
    } <= names


def test_suggestions_follow_the_selected_manager(monkeypatch, preference_store) -> None:
    owners = {"ananya": [SETTING], "karthik": [DRAFT]}
    seen = []

    def pending(manager_id=None):
        seen.append(manager_id)
        return owners.get(manager_id, [])

    monkeypatch.setattr(suggestions_ui, "pending_suggestions", pending)
    monkeypatch.setattr(
        suggestions_ui,
        "accept_suggestion",
        lambda *args: (_ for _ in ()).throw(
            LookupError("That suggestion is not one of yours."),
        ),
    )
    monkeypatch.setattr(
        suggestions_ui,
        "dismiss_suggestion",
        lambda *args: False,
    )
    _, _, items, *_ = suggestions_ui.refresh_for("ananya")
    assert [value for _, value in items["choices"]] == [str(SETTING.id)]
    # Karthik cannot accept or dismiss Ananya's suggestion.
    assert suggestions_ui.accept(str(SETTING.id), None, "karthik")[-1] == (
        "Not applied: That suggestion is not one of yours."
    )
    assert suggestions_ui.dismiss(str(SETTING.id), "karthik")[-1] == (
        "Not dismissed: that suggestion is no longer pending or is not yours."
    )
    assert set(seen) == {"ananya", "karthik"}


def test_review_panels_reload_with_the_selected_manager(ui_app) -> None:
    manager = next(
        block
        for block in ui_app.blocks.values()
        if isinstance(block, gr.State) and block.value == gradio_app.DEMO_MANAGER_ID
    )
    scoped = {
        suggestions_ui.refresh_for,
        suggestions_ui.select,
        suggestions_ui.accept,
        suggestions_ui.dismiss,
    }
    seen = set()
    for callback in ui_app.fns.values():
        if callback.fn in scoped:
            assert manager in callback.inputs, callback.fn.__name__
            seen.add(callback.fn)
    assert seen == scoped
