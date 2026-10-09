"""Scenario previews, saved-data inspection and demo controls."""

from dataclasses import dataclass
from html import escape
from typing import Any

import gradio as gr
import structlog
from sqlalchemy.exc import SQLAlchemyError

from constants import DEMO_MANAGER_ID
from service.conversations import (
    start_new_conversation,
)
from service.scenarios import (
    current_scenario,
    load_scenario,
    scenario_details,
)

logger = structlog.stdlib.get_logger(__name__)


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
    manager_id: str = DEMO_MANAGER_ID,
) -> tuple[dict[str, Any], list[dict], str, str, dict, dict, dict, dict]:
    """Load through the existing service, starting a new conversation on success."""
    try:
        context = load_scenario(key)
    except (KeyError, ValueError, SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load scenario %s", key)
        raise gr.Error(
            "Scenario could not be loaded. Check the database connection and migrations.",
        ) from exc
    try:
        start_new_conversation(manager_id=manager_id)
    except (SQLAlchemyError, RuntimeError):
        # The scenario is loaded; the next question continues the previous chat.
        logger.exception("Could not start a conversation after loading %s", key)
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


def _assistant_context(context: dict[str, Any] | None) -> str:
    """Identify the loaded scenario, independently of the demo preview."""
    if not context:
        return (
            '<div class="current-scenario"><span>Current scenario</span>'
            "<strong>Not available</strong></div>"
        )
    return (
        '<div class="current-scenario"><span>Current scenario</span>'
        f"<strong>{escape(context['title'])}</strong></div>"
    )


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


@dataclass
class ScenarioComponents:
    situation: gr.HTML
    scenario: gr.Dropdown
    load: gr.Button
    preview: gr.HTML
    refresh: gr.Button
    tables: list[gr.Dataframe]
    run_review_button: gr.Button
    mark_reviewed_button: gr.Button
    review_status: gr.Markdown
    answer_issues: gr.Dataframe


def build_scenarios(
    scenarios: list[dict[str, str]],
    choices: list[tuple[str, str]],
    default: str | None,
    scenarios_unavailable: bool,
) -> ScenarioComponents:
    with (
        gr.Tab("Demo tools", id="demo", render_children=True),
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
        with gr.Column(elem_id="review-admin", min_width=0):
            gr.HTML(
                '<div class="loader-heading"><h2>Daily review (admin)</h2>'
                "<p>Runs every day at 23:30 IST. Drafts the handover note, "
                "proposes settings, and lists answers that need a look.</p></div>",
                apply_default_css=False,
            )
            with gr.Row(elem_id="review-actions"):
                run_review_button = gr.Button(
                    "Run review now",
                    variant="primary",
                    scale=0,
                    min_width=160,
                )
                mark_reviewed_button = gr.Button(
                    "Mark all pending issues reviewed",
                    scale=0,
                    min_width=160,
                )
                review_status = gr.Markdown(elem_id="review-status")
            answer_issues = gr.Dataframe(
                value={"headers": [], "data": []},
                label="Answer issues",
                interactive=False,
                type="array",
                wrap=True,
                show_search="filter",
                elem_id="answer-issues",
            )

    return ScenarioComponents(
        situation,
        scenario,
        load,
        preview,
        refresh,
        tables,
        run_review_button,
        mark_reviewed_button,
        review_status,
        answer_issues,
    )
