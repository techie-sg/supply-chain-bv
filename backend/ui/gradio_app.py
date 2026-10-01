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
from service.scenarios import TIMEZONE, load_scenario, scenario_details, scenario_names

logger = logging.getLogger(__name__)
CSS_PATH = Path(__file__).with_name("gradio_app.css")
CUSTOM_CSS = CSS_PATH.read_text(encoding="utf-8")
THEME = gr.themes.Base(
    primary_hue="emerald",
    secondary_hue="emerald",
    neutral_hue="stone",
    # Gradio 6.29 compares the first font with built-in Font objects at launch.
    font=[gr.themes.Font("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
).set(
    body_background_fill="#f6f7f4",
    body_background_fill_dark="#141c19",
    body_text_color="#20372d",
    body_text_color_dark="#edf3ee",
    body_text_color_subdued="#66756b",
    body_text_color_subdued_dark="#a3b5a9",
    block_background_fill="#ffffff",
    block_background_fill_dark="#1b2620",
    block_border_color="#e3e8e1",
    block_border_color_dark="#34463a",
    block_radius="12px",
    block_label_background_fill="transparent",
    block_label_background_fill_dark="transparent",
    block_label_text_color="#66756b",
    block_label_text_color_dark="#a3b5a9",
    input_background_fill="#ffffff",
    input_background_fill_dark="#24332a",
    input_border_color="#dce3da",
    input_border_color_dark="#405749",
    button_primary_background_fill="#216446",
    button_primary_background_fill_dark="#b9ed8c",
    button_primary_background_fill_hover="#174e35",
    button_primary_background_fill_hover_dark="#c9f5a5",
    button_primary_text_color="#ffffff",
    button_primary_text_color_dark="#173c29",
    button_secondary_background_fill="#ffffff",
    button_secondary_background_fill_dark="#24332a",
    button_secondary_background_fill_hover="#f1f5ef",
    button_secondary_background_fill_hover_dark="#304536",
    button_secondary_text_color="#385443",
    button_secondary_text_color_dark="#dae9dc",
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
        "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    }
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{paths[name]}</svg>'
    )


CHAT_PLACEHOLDER = """
<div class="chat-welcome">
    <div class="route-illustration" aria-hidden="true">
        <span class="route-node node-start">A</span>
        <span class="route-line"></span>
        <span class="route-hub">D</span>
        <span class="route-node node-end">B</span>
        <span class="route-dot"></span>
    </div>
    <span class="welcome-kicker">A LITTLE CLARITY. A BETTER DISPATCH.</span>
    <h3>Good decisions start here.</h3>
    <p>Turn orders, rider availability, and your playbook into a clear next move.<br>
    Load a scenario, then ask away.</p>
</div>
"""


def _scenario_summary(context: dict[str, Any]) -> str:
    counts = context["counts"]
    weather = "Rain conditions" if context["is_raining"] else "Dry conditions"
    notes = {
        "orders": f"{counts['packed_waiting']} packed & waiting",
        "riders": f"{counts['available_riders']} available to dispatch",
        "hourly_metrics": "Historical performance",
        "zones": "Delivery coverage",
    }
    cards = "".join(
        f'<div class="stat"><div class="stat-top"><span>{label}</span>'
        f"{_icon(icon)}</div><strong>{counts[key]:02d}</strong>"
        f'<span class="stat-detail">{notes[key]}</span></div>'
        for key, label, icon in (
            ("orders", "Orders", "box"),
            ("riders", "Riders", "riders"),
            ("hourly_metrics", "Hourly records", "clock"),
            ("zones", "Zones", "zones"),
        )
    )
    weather_class = "rain" if context["is_raining"] else "dry"
    return (
        '<section class="scenario-preview" aria-label="Selected scenario preview">'
        '<div class="preview-heading"><div><span class="section-kicker">SCENARIO PREVIEW</span>'
        f"<h2>{escape(context['title'])}</h2></div>"
        f'<span class="weather-pill {weather_class}"><span aria-hidden="true">'
        f"{'☂' if context['is_raining'] else '☀'}</span> {weather}</span></div>"
        f'<div class="stats-grid">{cards}</div></section>'
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
        "minutes_since_last_break": "Since last break (min)",
        "sla_10min_pct": "10-min SLA (%)",
    }
    views = []
    for name in ("orders", "riders", "hourly_metrics", "zones"):
        table = context["tables"][name]
        columns = priority[name] + [
            header for header in table["headers"] if header not in priority[name]
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
    loaded_at = datetime.fromisoformat(context["as_of"]).astimezone(TIMEZONE)
    status = (
        '<div class="loaded-status"><span class="status-dot"></span><div>'
        '<span class="current-label">CURRENT SCENARIO</span>'
        f"<strong>{escape(context['title'])}</strong>"
        f"<span>{escape(context['store_id'])} · {loaded_at:%d %b %Y, %H:%M:%S} IST</span></div></div>"
    )
    return status, context, [], "", _scenario_summary(context), *_table_views(context)


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
            '<span class="brand-divider"></span><span class="header-label">Operations workspace</span>'
            '</div><div class="header-meta"><span class="demo-badge">SCENARIO LAB</span>'
            '<span class="header-avatar" aria-label="Dispatch workspace">DD</span></div></header>',
            css_template=CUSTOM_CSS,
        )
        current = gr.State(None)
        with gr.Row(elem_id="workspace-layout"):
            with gr.Column(scale=0, min_width=260, elem_id="scenario-sidebar"):
                gr.HTML(
                    '<div class="sidebar-heading"><span class="section-kicker">WORKSPACE</span>'
                    f"<h2>{_icon('zones')} Scenario controls</h2>"
                    "<p>Set the scene for your next decision.</p></div>",
                    css_template=CUSTOM_CSS,
                )
                scenario = gr.Dropdown(
                    choices=choices,
                    value=default,
                    label="Choose a scenario",
                    interactive=bool(choices),
                    filterable=False,
                    elem_id="scenario-picker",
                )
                load = gr.Button(
                    "Load scenario  →",
                    variant="primary",
                    interactive=bool(choices),
                    elem_id="load-scenario",
                )
                gr.Markdown(
                    "Loading replaces the demo data and starts a fresh conversation. "
                    "Changing the selection only updates the preview.",
                    elem_classes="sidebar-note",
                )
                load_status = gr.HTML(
                    '<div class="empty-status"><span class="current-label">CURRENT SCENARIO</span>'
                    '<strong><span class="status-dot idle"></span>No scenario loaded</strong>'
                    "<span>Load a scenario to connect its data to your conversation.</span></div>",
                    css_template=CUSTOM_CSS,
                    elem_id="load-status",
                )
                gr.HTML(
                    '<div class="sidebar-guide"><span class="section-kicker">FROM SNAPSHOT TO ACTION</span>'
                    "<ol><li><span>01</span><div><strong>Choose your scenario</strong><p>Explore a dispatch situation.</p></div></li>"
                    "<li><span>02</span><div><strong>Take a closer look</strong><p>Inspect orders, riders, and zones.</p></div></li>"
                    "<li><span>03</span><div><strong>Find your next move</strong><p>Ask for playbook-backed guidance.</p></div></li></ol></div>"
                    '<div class="sidebar-foot"><span class="mini-mark">D</span>'
                    "<div><strong>Built for the dispatch desk.</strong><span>Synthetic scenarios · Real decisions</span></div></div>",
                    css_template=CUSTOM_CSS,
                )
            with gr.Column(scale=1, min_width=0, elem_id="workspace-main"):
                gr.HTML(
                    '<div class="page-heading"><div><span class="section-kicker">THE DISPATCH DESK</span>'
                    "<h1>Keep every delivery moving.</h1>"
                    "<p>Your orders, your riders, your next best move. All in one place.</p></div>"
                    '<span class="workspace-tag">Dispatch copilot</span></div>',
                    css_template=CUSTOM_CSS,
                )
                preview = gr.HTML(
                    '<p class="muted">Choose a scenario to inspect its data.</p>',
                    css_template=CUSTOM_CSS,
                    elem_id="scenario-overview",
                )
                with gr.Tabs(elem_id="workspace-tabs"):
                    with (
                        gr.Tab("Dispatch assistant", id="chat"),
                        gr.Column(elem_id="assistant-panel"),
                    ):
                        with gr.Row(elem_id="assistant-heading"):
                            gr.HTML(
                                '<div class="assistant-title"><span class="assistant-icon">'
                                f"{_icon('spark')}</span><div><h2>Your dispatch copilot</h2>"
                                "<p>Clear next steps, grounded in your playbook.</p></div></div>",
                                css_template=CUSTOM_CSS,
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
                            height=300,
                            layout="bubble",
                            placeholder=CHAT_PLACEHOLDER,
                            buttons=["copy"],
                            elem_id="conversation",
                        )
                        with gr.Row(elem_id="starter-prompts"):
                            prompts = [
                                (
                                    "Find the priority  ↗",
                                    "What is the main dispatch problem in this scenario?",
                                ),
                                (
                                    "Explore safe batches  ↗",
                                    "Which orders can be batched safely?",
                                ),
                                (
                                    "Check rider breaks  ↗",
                                    "Which riders need a break?",
                                ),
                            ]
                            prompt_buttons = [
                                gr.Button(
                                    label, size="sm", elem_classes="prompt-button"
                                )
                                for label, _ in prompts
                            ]
                        with gr.Row(elem_id="message-composer"):
                            message = gr.Textbox(
                                label="Your question",
                                show_label=False,
                                placeholder="Ask about priorities, batches, or rider availability…",
                                lines=1,
                                max_lines=5,
                                container=False,
                                elem_id="message-input",
                            )
                            submit = gr.Button(
                                "Send  ↑",
                                variant="primary",
                                scale=0,
                                min_width=88,
                                elem_id="send-message",
                            )
                        gr.HTML(
                            '<div class="composer-note"><span>Grounded in your dispatch playbook</span>'
                            "<span>Enter to send</span></div>",
                            css_template=CUSTOM_CSS,
                        )
                    with (
                        gr.Tab("Scenario data", id="data"),
                        gr.Column(elem_id="data-panel"),
                    ):
                        gr.Markdown(
                            "### A closer look at your scenario\n"
                            "Explore the selected preview. Chat keeps using your loaded scenario until you load another.",
                            elem_id="data-heading",
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
                                            show_search="filter",
                                            show_row_numbers=True,
                                            pinned_columns=1,
                                            max_height=420,
                                            buttons=["fullscreen", "copy"],
                                            elem_classes="scenario-table",
                                        )
                                    )
                        gr.Markdown(
                            "Synthetic starting snapshots · Times shown in IST (+05:30) · Not a live feed",
                            elem_classes="panel-note",
                        )
                gr.HTML(
                    '<footer class="workspace-footer"><span>DISPATCHDESK<span class="footer-dot"> / </span>SCENARIO WORKSPACE</span>'
                    "<span>A clearer view. A better next move.</span></footer>",
                    css_template=CUSTOM_CSS,
                )
        for button, (_, question) in zip(prompt_buttons, prompts, strict=True):
            button.click(lambda q=question: q, outputs=message, queue=False).then(
                fn=None,
                js="() => document.querySelector('#message-input textarea')?.focus()",
            )
        if choices:
            app.load(prepare_scenario, inputs=scenario, outputs=[preview, *tables])
            scenario.change(
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
        )
        for event in (submit.click, message.submit):
            event(
                chat,
                inputs=[message, chatbot, current],
                outputs=[chatbot, message],
                concurrency_id="workspace",
                concurrency_limit=1,
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
