"""Editable response preferences; persistence stays in the service layer."""

from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import PERSONALIZATION_MAX_INSTRUCTIONS
from domain.personalization import FIELDS, NOTES, Profile
from service.personalization import manager_personalization

logger = structlog.stdlib.get_logger(__name__)


@dataclass
class PersonalizationComponents:
    controls: list[Any]
    snapshot: gr.State
    sources: gr.Markdown
    status: gr.Markdown

    def outputs(self) -> list[Any]:
        return [*self.controls, self.snapshot, self.sources, self.status]


def _values(profile: Profile, manager_id: str, status: str = "") -> tuple:
    sources = []
    for code, entry in profile.items():
        if not entry.get("value"):
            continue
        name = FIELDS[code].name if code in FIELDS else "Additional instructions"
        if entry.get("source") == "chat" and entry.get("conversation_id"):
            sources.append(
                f"- **{name}:** saved from chat. “{escape(entry.get('quote', ''))}”",
            )
        elif entry.get("source") == "suggestion":
            sources.append(f"- **{name}:** suggestion you accepted.")
        else:
            sources.append(f"- **{name}:** saved in Settings.")
        chat_id = entry.get("conversation_id")
        if chat_id:
            # Only validated local conversation links, never model-provided URLs.
            query = urlencode(
                {
                    "view": "assistant",
                    "manager": manager_id,
                    "chat": str(UUID(chat_id)),
                },
            )
            sources[-1] += f" [View conversation](?{query})"
    return (
        *(profile.get(code, {}).get("value") or "" for code in FIELDS),
        profile.get(NOTES, {}).get("value") or "",
        {"manager_id": manager_id, "profile": profile},
        "\n".join(sources)
        or "No saved preferences yet. DispatchDesk uses its default answer style.",
        status,
    )


def load(manager_id: str) -> tuple:
    try:
        return _values(manager_personalization(manager_id).profile(), manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load personalization", exc_info=True)
        # Never present an unavailable profile as an editable empty snapshot.
        return (
            *[gr.skip() for _ in range(len(FIELDS) + 1)],
            None,
            gr.skip(),
            "Personalization is unavailable right now. Refresh after the database is available.",
        )


def save(
    manager_id: str,
    snapshot: dict[str, Any] | None,
    *values: str | None,
) -> tuple:
    if snapshot is None or snapshot.get("manager_id") != manager_id:
        raise gr.Error("Refresh personalization before saving.")
    service = manager_personalization(manager_id)
    try:
        changed = service.save(
            dict(zip([*FIELDS, NOTES], values, strict=True)),
            snapshot["profile"],
        )
        return _values(
            service.profile(),
            manager_id,
            "Saved. Applies across your conversations."
            if changed
            else "No changes to save.",
        )
    except (ValueError, LookupError) as exc:
        raise gr.Error(str(exc)) from exc
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not save personalization")
        raise gr.Error("Personalization could not be saved. Please try again.") from exc


def build(manager: gr.State) -> PersonalizationComponents:
    gr.Markdown(
        "These preferences apply across your conversations until you change them. Choose **Use default** to remove a preference.",
    )
    controls: list[Any] = []
    with gr.Column(elem_classes="setting-card"):
        for code, field in FIELDS.items():
            controls.append(
                gr.Dropdown(
                    choices=[
                        ("Use default", ""),
                        *((name, value) for value, name in field.choices.items()),
                    ],
                    value="",
                    label=field.name,
                    elem_id=f"personalization-{code}",
                ),
            )
        controls.append(
            gr.Textbox(
                label="Additional instructions",
                lines=3,
                max_length=PERSONALIZATION_MAX_INSTRUCTIONS,
                placeholder="Use plain language. Explain the trade-off before suggesting extra spending.",
                info="Optional preferences for how you receive advice. Policy and operational settings still apply.",
                elem_id="personalization-instructions",
            ),
        )
    snapshot = gr.State(None)
    sources = gr.Markdown(elem_id="personalization-sources")
    gr.Markdown(
        "**When this changes:** only when you save here, explicitly ask in chat to remember/change/remove a lasting preference, or accept a suggestion. Summary refreshes can suggest preferences; they never apply them.",
    )
    with gr.Row():
        save_button = gr.Button(
            "Save personalization",
            variant="primary",
            scale=0,
            min_width=210,
        )
        refresh_button = gr.Button("Refresh", scale=0)
    status = gr.Markdown(elem_id="personalization-status")
    components = PersonalizationComponents(controls, snapshot, sources, status)
    save_button.click(
        save,
        inputs=[manager, snapshot, *controls],
        outputs=components.outputs(),
        concurrency_id="settings",
        concurrency_limit=1,
    )
    refresh_button.click(
        load,
        inputs=manager,
        outputs=components.outputs(),
        queue=False,
        show_progress="hidden",
    )
    return components
