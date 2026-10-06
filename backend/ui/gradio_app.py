"""Local DispatchDesk workspace for scenario data and dispatch guidance."""

import os
from collections.abc import Iterator
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4

import gradio as gr
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from domain.chat import ChatMessage
from logging_config import configure_logging
from service.agent import answer_with_tools
from service.rag import answer_question as _answer_rag
from service.scenarios import (
    current_scenario,
    load_scenario,
    scenario_details,
    scenario_names,
)

logger = structlog.stdlib.get_logger(__name__)
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
    <h1>Let’s make <span>the right call.</span></h1>
    <p>Ask a question or talk through a decision.<br>Get clear guidance grounded in your playbook.</p>
</div>
"""


PROCESSING_STATUS = """
<div class="thinking-indicator" role="status" aria-live="polite">
    <span class="thinking-spark" aria-hidden="true">✦</span>
    <span>Thinking…</span>
</div>
"""


SEND_MESSAGE_JS = """
(message, history) => {
    const busy = Boolean(message.trim());
    const update = (props) => ({__type__: 'update', ...props});
    return [
        busy ? message : '',
        busy ? [...(history || []), {role: 'user', content: [{type: 'text', text: message}]}] : (history || []),
        update({value: '', interactive: !busy}),
        update({interactive: !busy}),
        update({visible: busy})
    ];
}
"""

FINISH_CHAT_JS = """
() => [
    {__type__: 'update', visible: false},
    {__type__: 'update', interactive: true},
    {__type__: 'update', interactive: true}
]
"""


def _scenario_summary(context: dict[str, Any]) -> str:
    weather_pill = ""
    if "is_raining" in context:
        raining = context["is_raining"]
        weather = "Rain conditions" if raining else "Dry conditions"
        weather_class = "rain" if raining else "dry"
        weather_pill = (
            f'<span class="weather-pill {weather_class}"><span aria-hidden="true">'
            f"{'☂' if raining else '☀'}</span> {weather}</span>"
        )
    source = "SCENARIO PREVIEW" if "is_raining" in context else "CURRENT SCENARIO"
    return (
        '<section class="scenario-preview" aria-label="Scenario data">'
        f'<div class="preview-heading"><div><span class="section-kicker">{source}</span>'
        f"<h2>{escape(context['title'])}</h2>"
        f'<span class="store-badge">Store <strong>{escape(context["store_id"])}</strong></span></div>'
        f"{weather_pill}</div>"
        "</section>"
    )


def _table_views(context: dict[str, Any] | None) -> tuple[dict, dict, dict, dict]:
    if context is None:
        return (
            {"headers": [], "data": []},
            {"headers": [], "data": []},
            {"headers": [], "data": []},
            {"headers": [], "data": []},
        )
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
                        header,
                        header.replace("_", " ").title().replace(" Id", " ID"),
                    )
                    for header in columns
                ],
                "data": [[row[index] for index in indices] for row in table["data"]],
            },
        )
    return views[0], views[1], views[2], views[3]


def prepare_scenario(
    key: str,
    current: dict[str, Any] | None = None,
) -> tuple[str, dict, dict, dict, dict]:
    """Read current rows from the database; preview other starting scenarios."""
    try:
        if current and key == current["scenario_key"]:
            context = current_scenario()
            if context is None:
                raise RuntimeError("The saved scenario is no longer available")
        else:
            context = scenario_details(key)
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not prepare scenario %s", key)
        raise gr.Error(
            "Could not prepare this scenario. Check its configuration and database connection.",
        ) from exc
    return _scenario_summary(context), *_table_views(context)


def load_selected_scenario(
    key: str,
) -> tuple[dict[str, Any], list[dict], str, str, dict, dict, dict, dict]:
    """Load through the existing service, clearing stale conversation on success."""
    try:
        context = load_scenario(key)
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load scenario %s", key)
        raise gr.Error(
            "Scenario could not be loaded. Check the database connection and migrations.",
        ) from exc
    return (
        context,
        [],
        "",
        _scenario_summary(context),
        *_table_views(context),
    )


def restore_workspace() -> tuple:
    """Refresh saved data without replacing rows or changing the conversation."""
    try:
        context = current_scenario()
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError):
        logger.warning("Could not restore the saved scenario", exc_info=True)
        empty_message = "Saved scenario unavailable. Check the database connection and refresh again."
        context = None
    else:
        empty_message = "No saved scenario. Choose and load a scenario to get started."
    if context:
        return (
            context,
            gr.update(value=context["scenario_key"]),
            _scenario_summary(context),
            *_table_views(context),
        )
    return (
        None,
        gr.skip(),
        f'<p class="muted">{empty_message}</p>',
        *_table_views(None),
    )


def _conversation_history(history: list[dict]) -> list[ChatMessage]:
    """Convert Gradio's text blocks to provider-independent chat messages."""
    messages: list[ChatMessage] = []
    for message in history:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                block["text"]
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    return messages


def answer_question(
    message: str,
    *,
    history: list[ChatMessage] | None = None,
) -> str:
    """Route to the agent when a scenario is loaded, otherwise fall back to RAG."""
    scenario = current_scenario()
    if scenario:
        return answer_with_tools(
            message,
            store_id=scenario["store_id"],
            history=history,
        )
    return _answer_rag(message, history=history)


def chat(
    message: str,
    history: list[dict] | None,
) -> tuple[list[dict], str]:
    """Keep the user's draft and history intact if a backend request fails."""
    history = history or []
    if not message.strip():
        return history, ""
    request_id = uuid4().hex
    try:
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            answer = answer_question(message, history=_conversation_history(history))
    except (
        requests.RequestException,
        SQLAlchemyError,
        RuntimeError,
        ValueError,
    ) as exc:
        logger.exception("Assistant request failed", request_id=request_id)
        raise gr.Error(
            "The assistant is unavailable right now. Please try again.",
        ) from exc
    return history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer},
    ], ""


def respond_to_pending(
    message: str,
    history: list[dict] | None,
) -> Iterator[tuple[list[dict], str]]:
    """Answer the message already displayed by the browser without duplicating it."""
    history = history or []
    if not message.strip():
        yield history, ""
        return
    previous = (
        history[:-1] if history and history[-1].get("role") == "user" else history
    )
    try:
        yield chat(message, previous)
    except gr.Error:
        yield previous, message
        raise


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


def _situation_heading(key: str | None, scenarios: list[dict[str, str]]) -> str:
    scenario = next(
        (scenario for scenario in scenarios if scenario["key"] == key),
        {},
    )
    title = scenario.get(
        "title",
        key.replace("_", " ").replace("-", " ").title()
        if key
        else "No scenario loaded",
    )
    description = scenario.get(
        "description",
        "Choose and load a scenario to get started.",
    )
    return (
        '<div class="page-heading"><span class="section-kicker">CURRENT SITUATION</span>'
        f"<h1>{escape(title)}</h1>"
        f"<p>{escape(description)}</p></div>"
    )


def build_app() -> gr.Blocks:
    scenarios_unavailable = False
    try:
        scenarios = scenario_names()
    except (ValueError, OSError):
        logger.exception("Could not list scenarios; demo controls are unavailable")
        scenarios = []
        scenarios_unavailable = True
    choices = [(scenario["title"], scenario["key"]) for scenario in scenarios]
    default = next(
        (key for _, key in choices if key == "normal"),
        choices[0][1] if choices else None,
    )
    with gr.Blocks(title="DispatchDesk", delete_cache=(3600, 86400)) as app:
        gr.HTML(
            '<header class="desk-header"><a href="/?view=assistant" class="brand" aria-label="Reload Assistant"><span class="brand-mark">'
            f'{_icon("box")}</span><span>Dispatch<span class="brand-light">Desk</span></span>'
            "</a></header>",
            apply_default_css=False,
            elem_id="brand-home",
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
                        visible=False,
                        size="sm",
                        scale=0,
                        min_width=88,
                        elem_id="clear-chat",
                    )
                chatbot = gr.Chatbot(
                    label="Conversation",
                    show_label=False,
                    height="auto",
                    autoscroll=False,
                    layout="bubble",
                    placeholder=CHAT_PLACEHOLDER,
                    buttons=["copy"],
                    elem_id="conversation",
                )
                pending_message = gr.Textbox(visible="hidden", interactive=False)
                with gr.Column(elem_id="composer-dock", min_width=0):
                    processing = gr.HTML(
                        PROCESSING_STATUS,
                        visible=False,
                        apply_default_css=False,
                        elem_id="chat-processing",
                    )
                    with gr.Row(elem_id="message-composer"):
                        message = gr.Textbox(
                            label="Ask your dispatch assistant",
                            show_label=False,
                            placeholder="What's on your mind?",
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
                        "We're missing the 10-minute promise. Should I ask my riders to jump red lights and speed?",
                        "It's pouring and deliveries are late. Can I dock riders' pay for missing the delivery promise?",
                        "Packed orders are piling up. Can I batch frozen-item orders with other deliveries?",
                    ]
                    prompt_buttons = [
                        gr.Button(question, size="sm", elem_classes="prompt-button")
                        for question in prompts
                    ]
            with (
                gr.Tab("Demo tools", id="demo"),
                gr.Column(elem_id="demo-workspace", min_width=0),
            ):
                with gr.Row(elem_id="demo-heading"):
                    situation = gr.HTML(
                        _situation_heading(None, scenarios),
                        apply_default_css=False,
                        elem_id="scenario-situation",
                    )
                with gr.Column(elem_id="demo-layout", min_width=0):
                    with (
                        gr.Column(elem_id="scenario-toolbar", min_width=0),
                        gr.Row(elem_id="scenario-controls"),
                    ):
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
                    with gr.Column(scale=1, min_width=0, elem_id="data-panel"):
                        with gr.Row(elem_id="data-titlebar"):
                            gr.Markdown(
                                "### Inspect the data",
                                elem_id="data-heading",
                                scale=1,
                            )
                            preview = gr.HTML(
                                '<p class="muted">Scenarios unavailable. Check the scenario configuration. Assistant chat is still available.</p>'
                                if scenarios_unavailable
                                else '<p class="muted">Choose a scenario to inspect its data.</p>',
                                apply_default_css=False,
                                elem_id="scenario-overview",
                                scale=2,
                            )
                            refresh = gr.Button(
                                "↻ Refresh",
                                size="sm",
                                scale=0,
                                min_width=96,
                                elem_id="refresh-scenario",
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
                                        ),
                                    )
                        gr.Markdown(
                            "Synthetic starting snapshots · Refresh to see saved changes",
                            elem_classes="panel-note",
                        )
        app.load(_restore_tab, outputs=workspace, queue=False)
        workspace.change(fn=None, js=TAB_URL_JS)
        for button, question in zip(prompt_buttons, prompts, strict=True):
            button.click(lambda q=question: q, outputs=message, queue=False).then(
                fn=None,
                js="() => document.querySelector('#message-input textarea')?.focus()",
            )
        for event in (app.load, refresh.click):
            event(
                restore_workspace,
                outputs=[current, scenario, preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(_assistant_context, inputs=current, outputs=context_banner)
        current.change(
            lambda context: _situation_heading(
                context["scenario_key"] if context else None,
                scenarios,
            ),
            inputs=current,
            outputs=situation,
            queue=False,
            show_progress="hidden",
        )
        if choices:
            scenario.input(
                prepare_scenario,
                inputs=[scenario, current],
                outputs=[preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            )
        load.click(
            load_selected_scenario,
            inputs=scenario,
            outputs=[current, chatbot, message, preview, *tables],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).success(_assistant_context, inputs=current, outputs=context_banner)
        for event in (submit.click, message.submit):
            event(
                fn=None,
                js=SEND_MESSAGE_JS,
                inputs=[message, chatbot],
                outputs=[pending_message, chatbot, message, submit, processing],
                queue=False,
                show_progress="hidden",
            ).then(
                respond_to_pending,
                inputs=[pending_message, chatbot],
                outputs=[chatbot, message],
                show_progress="hidden",
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(
                fn=None,
                js=FINISH_CHAT_JS,
                outputs=[processing, submit, message],
                queue=False,
                show_progress="hidden",
            )
        chatbot.change(
            fn=None,
            js="""(history) => [
                {__type__: 'update', visible: !(history && history.length)},
                {__type__: 'update', visible: Boolean(history && history.length)},
                {__type__: 'update', placeholder: history && history.length
                    ? 'Ask a follow-up…' : "What's on your mind?"}
            ]""",
            inputs=chatbot,
            outputs=[suggestions, clear, message],
            queue=False,
            show_progress="hidden",
        ).then(
            fn=None,
            js="""async () => {
                await new Promise(requestAnimationFrame);
                await new Promise(requestAnimationFrame);
                const messages = document.querySelectorAll('#conversation .message.user, #conversation .message.bot');
                const latest = messages[messages.length - 1];
                if (!latest) return;
                const bounds = latest.getBoundingClientRect();
                const dock = document.querySelector('#composer-dock').getBoundingClientRect();
                const bottom = dock.top - 24;
                const target = bounds.height > bottom - 24
                    ? scrollY + bounds.top - 24
                    : scrollY + bounds.bottom - bottom;
                window.scrollTo({top: Math.max(0, target), behavior:
                    matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
            }""",
        )
        clear.click(
            lambda: ([], ""),
            outputs=[chatbot, message],
            queue=True,
            concurrency_id="workspace",
            concurrency_limit=1,
        )
    return app


if __name__ == "__main__":
    configure_logging()

app = build_app()


def main() -> None:
    configure_logging()
    port = int(os.environ.get("PORT", "7860"))
    logger.info("DispatchDesk starting", host="0.0.0.0", port=port)
    app.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=False,
        theme=THEME,
        css_paths=CSS_PATH,
        favicon_path=Path(__file__).with_name("favicon.svg"),
        footer_links=[],
    )


if __name__ == "__main__":
    main()
