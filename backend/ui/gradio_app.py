"""Local DispatchDesk workspace for scenario data and dispatch guidance."""

import logging
import os
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import gradio as gr
import requests
from sqlalchemy.exc import SQLAlchemyError

from service.rag import answer_question
from service.scenarios import (
    TIMEZONE,
    current_scenario,
    load_scenario,
    scenario_details,
    scenario_names,
)

logger = logging.getLogger(__name__)
CSS_PATH = Path(__file__).with_name("gradio_app.css")
THEME = gr.themes.Base(
    primary_hue="indigo",
    secondary_hue="violet",
    neutral_hue="slate",
    # Gradio 6.29 compares the first font with built-in Font objects at launch.
    font=[gr.themes.GoogleFont("DM Sans", weights=[400, 500, 600, 700]), "sans-serif"],
).set(
    body_background_fill="#18181a",
    body_background_fill_dark="#18181a",
    body_text_color="#eeedf0",
    body_text_color_dark="#eeedf0",
    body_text_color_subdued="#c0bec7",
    body_text_color_subdued_dark="#c0bec7",
    block_background_fill="#232325",
    block_background_fill_dark="#232325",
    block_border_color="#363539",
    block_border_color_dark="#363539",
    block_radius="12px",
    block_label_background_fill="transparent",
    block_label_background_fill_dark="transparent",
    block_label_text_color="#c0bec7",
    block_label_text_color_dark="#c0bec7",
    input_background_fill="#232325",
    input_background_fill_dark="#232325",
    input_border_color="#3e3c43",
    input_border_color_dark="#3e3c43",
    button_primary_background_fill="#b4a3de",
    button_primary_background_fill_dark="#b4a3de",
    button_primary_background_fill_hover="#c5b6e9",
    button_primary_background_fill_hover_dark="#c5b6e9",
    button_primary_text_color="#211c2c",
    button_primary_text_color_dark="#211c2c",
    button_primary_border_color="#b4a3de",
    button_primary_border_color_dark="#b4a3de",
    button_secondary_background_fill="#232325",
    button_secondary_background_fill_dark="#232325",
    button_secondary_background_fill_hover="#302c38",
    button_secondary_background_fill_hover_dark="#302c38",
    button_secondary_text_color="#ded9e7",
    button_secondary_text_color_dark="#ded9e7",
    button_border_width="1px",
    button_large_radius="10px",
)


def _icon(name: str) -> str:
    """Small, local line icons; no external font or image dependency."""
    paths = {
        "box": '<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Z"/><path d="m3 8 9 5 9-5M12 13v8M7.5 5.5l9 5"/>',
        "riders": '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5.7"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "zones": '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6ZM9 3v15M15 6v15"/>',
        "spark": '<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/>',
    }
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{paths[name]}</svg>'
    )


CHAT_PLACEHOLDER = """
<div class="chat-welcome">
    <div class="welcome-orbit" aria-hidden="true"><span class="welcome-emblem"></span></div>
    <h1>What needs your <span>attention?</span></h1>
    <p>Make sense of delays. Find a safer, smarter next move.</p>
</div>
"""


def _scenario_summary(context: dict[str, Any]) -> str:
    weather = "Rain conditions" if context["is_raining"] else "Dry conditions"
    weather_class = "rain" if context["is_raining"] else "dry"
    return (
        '<section class="scenario-preview" aria-label="Selected scenario preview">'
        '<div class="preview-heading"><div><span class="section-kicker">SCENARIO PREVIEW</span>'
        f"<h2>{escape(context['title'])}</h2>"
        f'<span class="store-badge">Store <strong>{escape(context["store_id"])}</strong></span></div>'
        f'<span class="weather-pill {weather_class}"><span aria-hidden="true">'
        f"{'☂' if context['is_raining'] else '☀'}</span> {weather}</span></div>"
        "</section>"
    )


def _table_views(context: dict[str, Any]) -> tuple[dict, dict, dict, dict]:
    priority = {
        "orders": [
            "order_id",
            "status",
            "zone_id",
            "item_count",
            "has_frozen_items",
            "assigned_rider_id",
            "placed_at",
        ],
        "riders": [
            "rider_id",
            "name",
            "status",
            "current_zone",
            "employment_type",
            "hours_on_shift",
            "minutes_since_last_break",
            "deliveries_today",
            "eta_back_min",
        ],
        "hourly_metrics": [
            "date",
            "hour",
            "orders",
            "avg_pick_pack_min",
            "avg_rider_wait_min",
            "avg_ride_min",
            "sla_10min_pct",
            "riders_online",
            "rain_flag",
        ],
        "zones": [
            "zone_id",
            "zone_name",
            "distance_from_store_km",
            "avg_ride_min_dry",
            "avg_ride_min_rain",
        ],
    }
    labels = {
        "has_frozen_items": "Frozen items",
        "assigned_rider_id": "Assigned rider",
        "item_count": "Items",
        "minutes_since_last_break": "Break ago (min)",
        "current_zone": "Zone",
        "employment_type": "Employment",
        "hours_on_shift": "Shift (h)",
        "deliveries_today": "Deliveries",
        "eta_back_min": "Return (min)",
        "scenario_key": "Scenario",
        "avg_pick_pack_min": "Pick / pack (min)",
        "avg_rider_wait_min": "Rider wait (min)",
        "avg_ride_min": "Ride (min)",
        "riders_online": "Riders online",
        "rain_flag": "Rain",
        "distance_from_store_km": "Distance (km)",
        "avg_ride_min_dry": "Dry ride (min)",
        "avg_ride_min_rain": "Rain ride (min)",
        "sla_10min_pct": "10-min SLA (%)",
    }
    views = []
    for name in ("orders", "riders", "hourly_metrics", "zones"):
        table = context["tables"][name]
        columns = priority[name] + [
            header
            for header in table["headers"]
            if header not in priority[name] and header not in {"as_of", "store_id"}
        ]
        indices = [table["headers"].index(column) for column in columns]
        views.append(
            {
                "headers": [
                    labels.get(
                        header, header.replace("_", " ").title().replace(" Id", " ID")
                    )
                    for header in columns
                ],
                "data": [[row[index] for index in indices] for row in table["data"]],
            }
        )
    return views[0], views[1], views[2], views[3]


def prepare_scenario(key: str) -> tuple[str, dict, dict, dict, dict]:
    """Inspect the selected scenario without changing the loaded snapshot."""
    try:
        context = scenario_details(key)
    except (KeyError, ValueError) as exc:
        logger.exception("Could not prepare scenario %s", key)
        raise gr.Error(
            "Could not prepare this scenario. Check its configuration."
        ) from exc
    return _scenario_summary(context), *_table_views(context)


def load_selected_scenario(
    key: str,
) -> tuple[str, dict[str, Any], list[dict], str, str, dict, dict, dict, dict]:
    """Load through the existing service, clearing stale conversation on success."""
    try:
        context = load_scenario(key)
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load scenario %s", key)
        raise gr.Error(
            "Scenario could not be loaded. Check the database connection and migrations."
        ) from exc
    return (
        _loaded_status(context),
        context,
        [],
        "",
        _scenario_summary(context),
        *_table_views(context),
    )


def _loaded_status(context: dict[str, Any]) -> str:
    loaded_at = datetime.fromisoformat(context["as_of"]).astimezone(TIMEZONE)
    return (
        '<div class="loaded-status"><span class="status-dot"></span><div>'
        '<span class="current-label">CURRENT SCENARIO</span>'
        f"<strong>{escape(context['title'])}</strong>"
        f"<span>{escape(context['store_id'])} · {loaded_at:%d %b %Y, %H:%M:%S} IST</span></div></div>"
    )


def restore_workspace(default_key: str) -> tuple:
    """Reconnect a new session to saved data; never load or replace rows here."""
    try:
        context = current_scenario()
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError):
        logger.warning("Could not restore the saved scenario")
        status = '<div class="empty-status"><strong>Saved scenario unavailable</strong><span>Check the database connection, or choose a scenario to preview.</span></div>'
        context = None
    else:
        status = (
            _loaded_status(context)
            if context
            else '<div class="empty-status"><strong>No saved scenario</strong><span>Choose and load a scenario to get started.</span></div>'
        )
    if context:
        return (
            status,
            context,
            gr.update(value=context["scenario_key"]),
            _scenario_summary(context),
            *_table_views(context),
        )
    return status, None, gr.skip(), *prepare_scenario(default_key)


def chat(
    message: str,
    history: list[dict] | None,
    scenario_context: dict[str, Any] | None = None,
) -> tuple[list[dict], str]:
    """Keep the user's draft and history intact if a backend request fails."""
    history = history or []
    message = message.strip()
    if not message:
        return history, ""
    try:
        answer = (
            answer_question(message, scenario_context=scenario_context)
            if scenario_context
            else answer_question(message)
        )
    except (
        requests.RequestException,
        SQLAlchemyError,
        RuntimeError,
        ValueError,
    ) as exc:
        logger.exception("Assistant request failed")
        raise gr.Error(
            "The assistant is unavailable right now. Please try again."
        ) from exc
    return history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer},
    ], ""


def _assistant_context(context: dict[str, Any] | None) -> str:
    """Identify the loaded scenario, independently of the demo preview."""
    if not context:
        return '<div class="current-scenario"><span>Scenario unavailable</span></div>'
    return (
        '<div class="current-scenario"><span>Current scenario</span>'
        f"<strong>{escape(context['title'])}</strong></div>"
    )


def _restore_tab(request: gr.Request) -> dict:
    view = request.query_params.get("view", "assistant")
    return gr.update(selected="demo" if view == "demo" else "assistant")


TAB_URL_JS = """
async () => {
    await new Promise(requestAnimationFrame);
    const tab = document.querySelector(
        '#workspace-tabs > .tab-wrapper [role="tab"][aria-selected="true"]'
    );
    const view = tab?.dataset.tabId;
    if (view !== 'assistant' && view !== 'demo') return;
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    window.history.replaceState(null, '', url);
}
"""


def build_app() -> gr.Blocks:
    scenarios = scenario_names()
    choices = [(scenario["title"], scenario["key"]) for scenario in scenarios]
    default = next(
        (key for _, key in choices if key == "normal"),
        choices[0][1] if choices else None,
    )
    with gr.Blocks(title="DispatchDesk", delete_cache=(3600, 86400)) as app:
        gr.HTML(
            '<header class="desk-header"><div class="brand"><span class="brand-mark">'
            f'{_icon("box")}</span><span>Dispatch<span class="brand-light">Desk</span></span>'
            "</div></header>",
            apply_default_css=False,
        )
        current = gr.State(None)
        with gr.Tabs(selected="assistant", elem_id="workspace-tabs") as workspace:
            with (
                gr.Tab("Assistant", id="assistant"),
                gr.Column(elem_id="manager-workspace", min_width=0),
                gr.Column(elem_id="assistant-panel", min_width=0),
            ):
                with gr.Row(elem_id="assistant-heading"):
                    context_banner = gr.HTML(
                        '<div class="current-scenario"><span>Checking scenario…</span></div>'
                        if choices
                        else _assistant_context(None),
                        apply_default_css=False,
                        elem_id="assistant-context",
                        scale=0,
                        min_width=0,
                    )
                    clear = gr.Button(
                        "Clear chat",
                        size="sm",
                        scale=0,
                        min_width=88,
                        elem_id="clear-chat",
                    )
                chatbot = gr.Chatbot(
                    label="Conversation",
                    show_label=False,
                    height="calc(100dvh - 320px)",
                    layout="bubble",
                    placeholder=CHAT_PLACEHOLDER,
                    buttons=["copy"],
                    elem_id="conversation",
                )
                with gr.Row(elem_id="message-composer"):
                    message = gr.Textbox(
                        label="Ask your dispatch assistant",
                        show_label=False,
                        placeholder="What’s happening at your store?",
                        lines=1,
                        max_lines=6,
                        container=False,
                        elem_id="message-input",
                    )
                    submit = gr.Button(
                        "Send",
                        variant="primary",
                        scale=0,
                        min_width=88,
                        elem_id="send-message",
                    )
                with gr.Row(elem_id="starter-prompts") as suggestions:
                    prompts = [
                        (
                            "Resolve a backlog\nFind the bottleneck and what to do first.",
                            "Orders are backing up. What is causing the delay, and what should I do first?",
                        ),
                        (
                            "Understand an SLA drop\nCompare shifts and uncover the cause.",
                            "Why did our 10-minute SLA compliance fall between 8 and 10pm last night compared with the night before?",
                        ),
                        (
                            "Plan a safe batch\nCheck orders, routes, and rider readiness.",
                            "Which waiting orders can we batch safely? Explain any exclusions and check rider availability and breaks.",
                        ),
                    ]
                    prompt_buttons = [
                        gr.Button(label, size="sm", elem_classes="prompt-button")
                        for label, _ in prompts
                    ]
            with (
                gr.Tab("Demo tools", id="demo"),
                gr.Column(elem_id="demo-workspace", min_width=0),
            ):
                with gr.Row(elem_id="demo-heading"):
                    gr.HTML(
                        '<div class="page-heading"><span class="section-kicker">DEMO TOOLS</span>'
                        "<h1>Set up a situation.</h1><p>Load a simulated dataset and inspect the data behind the assistant.</p></div>",
                        apply_default_css=False,
                    )
                    back = gr.Button(
                        "Back to assistant",
                        scale=0,
                        min_width=160,
                        elem_id="back-to-assistant",
                    )
                with gr.Column(elem_id="demo-layout", min_width=0):
                    with gr.Column(elem_id="scenario-toolbar", min_width=0):
                        with gr.Row(elem_id="scenario-controls"):
                            gr.HTML(
                                '<div class="loader-heading"><h2>Scenario loader</h2>'
                                "<p>Choose a situation to inspect or load.</p></div>",
                                apply_default_css=False,
                                scale=1,
                                min_width=230,
                            )
                            scenario = gr.Dropdown(
                                choices=choices,
                                value=default,
                                label="Choose a scenario",
                                show_label=False,
                                interactive=bool(choices),
                                filterable=False,
                                scale=0,
                                min_width=300,
                                elem_id="scenario-picker",
                            )
                            load = gr.Button(
                                "Load scenario",
                                variant="primary",
                                interactive=bool(choices),
                                scale=0,
                                min_width=168,
                                elem_id="load-scenario",
                            )
                        with gr.Row(elem_id="scenario-info"):
                            load_status = gr.HTML(
                                '<div class="empty-status"><strong>Checking saved scenario…</strong></div>'
                                if choices
                                else '<div class="empty-status"><strong>No scenarios available</strong></div>',
                                apply_default_css=False,
                                scale=1,
                                min_width=260,
                                elem_id="load-status",
                            )
                            gr.Markdown(
                                "Selecting changes the preview. Loading replaces the demo data and clears the chat.",
                                elem_classes="loader-note",
                                scale=1,
                            )
                    with gr.Column(scale=1, min_width=0, elem_id="data-panel"):
                        with gr.Row(elem_id="data-titlebar"):
                            gr.Markdown(
                                "### Inspect the data",
                                elem_id="data-heading",
                                scale=1,
                            )
                            preview = gr.HTML(
                                '<p class="muted">Choose a scenario to inspect its data.</p>',
                                apply_default_css=False,
                                elem_id="scenario-overview",
                                scale=2,
                            )
                        tables = []
                        with gr.Tabs(elem_id="data-tabs"):
                            for title in (
                                "Orders",
                                "Riders",
                                "Hourly metrics",
                                "Zones",
                            ):
                                with gr.Tab(title):
                                    tables.append(
                                        gr.Dataframe(
                                            value={"headers": [], "data": []},
                                            label=title,
                                            show_label=False,
                                            interactive=False,
                                            type="array",
                                            datatype="auto",
                                            wrap=False,
                                            show_search="filter",
                                            show_row_numbers=True,
                                            pinned_columns=1,
                                            max_height="calc(100dvh - 390px)",
                                            buttons=["fullscreen", "copy"],
                                            elem_classes="scenario-table",
                                        )
                                    )
                        gr.Markdown(
                            "Synthetic starting snapshots · Times shown in IST (+05:30) · Not a live feed",
                            elem_classes="panel-note",
                        )
        app.load(_restore_tab, outputs=workspace, queue=False)
        workspace.change(fn=None, js=TAB_URL_JS)
        back.click(
            lambda: gr.update(selected="assistant"),
            outputs=workspace,
            queue=False,
        )
        for button, (_, question) in zip(prompt_buttons, prompts, strict=True):
            button.click(lambda q=question: q, outputs=message, queue=False).then(
                fn=None,
                js="() => document.querySelector('#message-input textarea')?.focus()",
            )
        if choices:
            app.load(
                restore_workspace,
                inputs=scenario,
                outputs=[load_status, current, scenario, preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(_assistant_context, inputs=current, outputs=context_banner)
            scenario.input(
                prepare_scenario,
                inputs=scenario,
                outputs=[preview, *tables],
                concurrency_limit=1,
            )
        load.click(
            load_selected_scenario,
            inputs=scenario,
            outputs=[load_status, current, chatbot, message, preview, *tables],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).success(_assistant_context, inputs=current, outputs=context_banner)
        for event in (submit.click, message.submit):
            event(
                chat,
                inputs=[message, chatbot, current],
                outputs=[chatbot, message],
                show_progress="minimal",
                show_progress_on=chatbot,
                concurrency_id="workspace",
                concurrency_limit=1,
            )
        chatbot.change(
            lambda history: gr.update(visible=not bool(history)),
            inputs=chatbot,
            outputs=suggestions,
            queue=False,
            show_progress="hidden",
        )
        clear.click(lambda: ([], ""), outputs=[chatbot, message], queue=False)
    return app


app = build_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    app.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
        share=False,
        theme=THEME,
        css_paths=CSS_PATH,
        footer_links=[],
    )


if __name__ == "__main__":
    main()
