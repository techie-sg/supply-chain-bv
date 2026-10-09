"""Editable personal context; persistence stays in the service layer."""

from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import PERSONALIZATION_MAX_INSTRUCTIONS
from domain.personalization import FIELDS, NOTES, Profile, describe
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
        name = FIELDS[code].name if code in FIELDS else "Personal context"
        if entry.get("source") == "dreaming":
            sources.append(
                f"- **{name}:** learned during conversation review. “{escape(entry.get('quote', ''))}”",
            )
        elif entry.get("source") == "chat" and entry.get("conversation_id"):
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
        profile.get(NOTES, {}).get("value")
        or "\n".join(
            describe(code, entry["value"])
            for code, entry in profile.items()
            if code in FIELDS and entry.get("value")
        ),
        {"manager_id": manager_id, "profile": profile},
        "\n".join(sources) or "No personal context saved yet.",
        status,
    )


def load(manager_id: str) -> tuple:
    try:
        return _values(manager_personalization(manager_id).profile(), manager_id)
    except (SQLAlchemyError, RuntimeError):
        logger.warning("Could not load personalization", exc_info=True)
        # Never present an unavailable profile as an editable empty snapshot.
        return (
            *[gr.skip() for _ in range(1)],
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
            {
                NOTES: values[0],
                **{code: None for code in FIELDS if code in snapshot["profile"]},
            },
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
        "Dreaming remembers relevant details and instructions from your chats. Review, edit or clear your personal context here.",
    )
    controls: list[Any] = []
    with gr.Column(elem_classes="setting-card"):
        controls.append(
            gr.Textbox(
                label="Personal context and instructions",
                lines=8,
                max_length=PERSONALIZATION_MAX_INSTRUCTIONS,
                placeholder="Use plain language. Explain the trade-off before suggesting extra spending.",
                info="Your name, contact details, working preferences and personal instructions. Policy and operational settings still apply.",
                elem_id="personalization-instructions",
            ),
        )
    snapshot = gr.State(None)
    sources = gr.Markdown(elem_id="personalization-sources")
    gr.Markdown(
        "**When this changes:** save here, or let dreaming learn clear or repeated preferences from your chats after summarization. Dreaming saves verified preferences automatically, without approval. Chat does not save them immediately. Ordinary questions do not change your preferences; review keeps existing details unless you correct or ask to forget them.",
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
