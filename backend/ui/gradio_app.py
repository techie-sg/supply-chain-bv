"""Compose and launch the DispatchDesk Gradio application."""

import os
from pathlib import Path

import gradio as gr
import structlog

from constants import DEMO_MANAGER_ID
from logging_config import configure_logging, configure_uvicorn_logging
from service.conversations import current_conversation_id
from service.scenarios import scenario_names
from ui import chat as chat_ui
from ui import navigation as navigation_ui
from ui import scenarios as scenarios_ui
from ui import settings
from ui import sidebar as sidebar_ui
from ui import suggestions as suggestions_ui
from ui import summary as summary_ui
from ui.theme import THEME

logger = structlog.stdlib.get_logger(__name__)
CSS_PATH = Path(__file__).with_name("gradio_app.css")


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
        current = gr.State(None)
        active_chat = gr.State(None)
        chat_location = gr.JSON(visible=False)
        # The selected manager's id; chats, settings and proposals follow it.
        manager = gr.State(DEMO_MANAGER_ID)
        # Setting changes proposed in chat, waiting for Confirm or Cancel.
        pending_changes = gr.State([])
        sidebar_components = sidebar_ui.build_sidebar()
        new_chat = sidebar_components.new_chat
        edit_settings = sidebar_components.edit_settings
        demo_navigation = sidebar_components.demo_navigation
        history_list = sidebar_components.history_list
        suggestions_entry = sidebar_components.suggestions_entry
        suggestions_entry_text = sidebar_components.suggestions_entry_text
        review_suggestions = sidebar_components.review_suggestions
        settings_summary = sidebar_components.settings_summary
        context_banner = sidebar_components.context_banner
        manager_picker = sidebar_components.manager_picker
        badge = sidebar_components.badge
        with gr.Tabs(selected="assistant", elem_id="workspace-tabs") as workspace:
            chat_components = chat_ui.build_chat()
            chatbot = chat_components.chatbot
            pending_message = chat_components.pending_message
            pending_box = chat_components.pending_box
            pending_html = chat_components.pending_html
            confirm_changes = chat_components.confirm_changes
            cancel_changes = chat_components.cancel_changes
            processing = chat_components.processing
            summary_status = chat_components.summary_status
            summary_text = chat_components.summary_text
            summarize_button = chat_components.summarize_button
            message = chat_components.message
            summary_trigger = chat_components.summary_trigger
            submit = chat_components.submit
            suggestions = chat_components.suggestions
            prompts = chat_components.prompts
            prompt_buttons = chat_components.prompt_buttons
            with (
                gr.Tab("Settings", id="settings"),
                gr.Column(elem_id="settings-workspace", min_width=0),
            ):
                settings_form = settings.build(manager, summary=settings_summary)
            scenarios_components = scenarios_ui.build_scenarios(
                scenarios,
                choices,
                default,
                scenarios_unavailable,
            )
            situation = scenarios_components.situation
            scenario = scenarios_components.scenario
            load = scenarios_components.load
            preview = scenarios_components.preview
            refresh = scenarios_components.refresh
            tables = scenarios_components.tables
            run_review_button = scenarios_components.run_review_button
            mark_reviewed_button = scenarios_components.mark_reviewed_button
            review_status = scenarios_components.review_status
            answer_issues = scenarios_components.answer_issues
        app.load(navigation_ui._restore_tab, outputs=workspace, queue=False)
        suggestion_components = settings_form.suggestions
        assert suggestion_components is not None
        suggestion_items = suggestion_components.items
        suggestion_detail = suggestion_components.detail
        suggestion_note = suggestion_components.note
        accept_suggestion = suggestion_components.accept
        dismiss_suggestion = suggestion_components.dismiss
        memory_view = settings_form.memory
        suggestion_outputs = suggestion_components.outputs(
            suggestions_entry,
            suggestions_entry_text,
        )
        assert settings_form.nav is not None
        review_suggestions.click(
            navigation_ui.open_settings,
            outputs=workspace,
            js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS,
            queue=False,
            show_progress="hidden",
        ).then(
            navigation_ui.show_suggestions,
            outputs=[settings_form.nav, *settings_form.category_outputs],
            queue=False,
            show_progress="hidden",
        )
        suggestion_items.input(
            suggestions_ui.select,
            inputs=[suggestion_items, manager],
            outputs=[suggestion_detail, suggestion_note],
        )
        accept_suggestion.click(
            suggestions_ui.accept,
            inputs=[suggestion_items, suggestion_note, manager],
            outputs=suggestion_outputs,
            concurrency_id="settings",
            concurrency_limit=1,
        ).then(
            settings.load_settings,
            inputs=manager,
            outputs=settings_form.outputs(),
        ).then(settings.load_summary, inputs=manager, outputs=settings_summary)
        dismiss_suggestion.click(
            suggestions_ui.dismiss,
            inputs=[suggestion_items, manager],
            outputs=suggestion_outputs,
            concurrency_id="settings",
            concurrency_limit=1,
        )
        run_review_button.click(
            suggestions_ui.run_review_now,
            inputs=manager,
            outputs=[review_status, answer_issues],
            concurrency_id="review",
            concurrency_limit=1,
        ).then(
            suggestions_ui.refresh_for,
            inputs=manager,
            outputs=suggestion_outputs,
        ).then(settings.load_memory, inputs=manager, outputs=memory_view)
        mark_reviewed_button.click(
            suggestions_ui.mark_issues_reviewed,
            inputs=manager,
            outputs=[review_status, answer_issues],
            concurrency_id="review",
            concurrency_limit=1,
        )
        # Sidebar navigation selects the same workspace panels and URL state.
        demo_navigation.click(
            lambda: gr.update(selected="demo"),
            outputs=workspace,
            js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS,
            queue=False,
            show_progress="hidden",
        )
        edit_settings.click(
            navigation_ui.open_settings,
            outputs=workspace,
            js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS,
            queue=False,
            show_progress="hidden",
        )
        summary_outputs = [
            summary_trigger,
            summary_status,
            summary_text,
            summarize_button,
        ]
        summarize_button.click(
            summary_ui.summarize_now,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
            # A manual update must finish before switching to another chat.
            concurrency_id="workspace",
            concurrency_limit=1,
            show_progress="hidden",
            js="""(...args) => {
                const button = document.querySelector('#summarize-now');
                button.setAttribute('aria-busy', 'true');
                button.disabled = true;
                return args;
            }""",
        ).then(
            fn=None,
            js="""() => {
                const button = document.querySelector('#summarize-now');
                button.removeAttribute('aria-busy');
                button.disabled = false;
            }""",
            queue=False,
            show_progress="hidden",
        )

        def show_manager(event, linked=False):
            """After the manager is set: their chat, chats, summary and settings."""
            return (
                event.then(
                    sidebar_ui.restore_conversation
                    if linked
                    else sidebar_ui.restore_latest_conversation,
                    inputs=manager,
                    outputs=[chatbot, active_chat],
                    concurrency_id="workspace",
                    concurrency_limit=1,
                )
                .then(
                    sidebar_ui.conversation_choices,
                    inputs=[manager, active_chat],
                    outputs=history_list,
                )
                .then(
                    summary_ui.summary_card,
                    inputs=[manager, active_chat],
                    outputs=summary_outputs,
                )
                .then(
                    settings.load_settings,
                    inputs=manager,
                    outputs=settings_form.outputs(),
                    concurrency_id="settings",
                    concurrency_limit=1,
                )
                .then(settings.load_summary, inputs=manager, outputs=settings_summary)
                .then(
                    suggestions_ui.refresh_for,
                    inputs=manager,
                    outputs=suggestion_outputs,
                )
                .then(
                    suggestions_ui.issues_table,
                    inputs=manager,
                    outputs=answer_issues,
                )
                .then(settings.load_memory, inputs=manager, outputs=memory_view)
            )

        show_manager(
            app.load(
                sidebar_ui.restore_manager,
                outputs=[manager, manager_picker, badge],
                queue=False,
            ),
            linked=True,
        )
        # A proposal belongs to the manager it was made for.
        show_manager(
            manager_picker.input(
                sidebar_ui.select_manager,
                inputs=manager_picker,
                outputs=[manager, badge],
                queue=False,
            )
            .then(list, outputs=pending_changes, queue=False)
            .then(fn=None, js=sidebar_ui.MANAGER_URL_JS, inputs=manager_picker),
        ).then(fn=None, js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS)
        app.load(fn=None, js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS)
        app.load(fn=None, js=navigation_ui.CHAT_NAVIGATION_JS)
        app.load(fn=None, js=summary_ui.SUMMARY_POPOVER_JS)
        history_list.input(
            sidebar_ui.open_conversation,
            inputs=[history_list, manager],
            outputs=[chatbot, message, active_chat, workspace],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(fn=None, js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS)
        active_chat.change(
            fn=None,
            js=summary_ui.CLOSE_SUMMARY_JS,
            queue=False,
        )
        active_chat.change(
            sidebar_ui.conversation_choices,
            inputs=[manager, active_chat],
            outputs=history_list,
        ).then(
            summary_ui.summary_card,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
        ).then(
            sidebar_ui.conversation_location,
            inputs=[manager, active_chat],
            outputs=chat_location,
        )
        chat_location.change(
            fn=None,
            js=navigation_ui.CHAT_URL_JS,
            inputs=chat_location,
        )
        workspace.change(fn=None, js=navigation_ui.TAB_URL_JS)
        for button, question in zip(prompt_buttons, prompts, strict=True):
            button.click(lambda q=question: q, outputs=message, queue=False).then(
                fn=None,
                js="() => document.querySelector('#message-input textarea')?.focus()",
            )
        for event in (app.load, refresh.click):
            event(
                scenarios_ui.restore_workspace,
                outputs=[current, scenario, preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(
                scenarios_ui._assistant_context,
                inputs=current,
                outputs=context_banner,
            )
        current.change(
            lambda context: scenarios_ui._situation_heading(
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
                scenarios_ui.prepare_scenario,
                inputs=[scenario, current],
                outputs=[preview, *tables],
                concurrency_id="workspace",
                concurrency_limit=1,
            )
        load.click(
            scenarios_ui.load_selected_scenario,
            inputs=[scenario, manager],
            outputs=[current, chatbot, message, preview, *tables],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).success(
            scenarios_ui._assistant_context,
            inputs=current,
            outputs=context_banner,
        ).then(
            current_conversation_id,
            inputs=manager,
            outputs=active_chat,
        ).then(
            sidebar_ui.conversation_choices,
            inputs=[manager, active_chat],
            outputs=history_list,
        ).then(
            summary_ui.summary_card,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
        )
        for event in (submit.click, message.submit):
            event(
                fn=None,
                js=chat_ui.SEND_MESSAGE_JS,
                inputs=[message, chatbot],
                outputs=[pending_message, chatbot, message, submit, processing],
                queue=False,
                show_progress="hidden",
            ).then(
                chat_ui.respond_to_pending,
                inputs=[pending_message, chatbot, manager, active_chat],
                outputs=[chatbot, message, pending_changes],
                show_progress="hidden",
                concurrency_id="workspace",
                concurrency_limit=1,
            ).then(
                fn=None,
                js=chat_ui.FINISH_CHAT_JS,
                outputs=[processing, submit, message],
                queue=False,
                show_progress="hidden",
            ).then(
                lambda manager_id, selected: (
                    selected or current_conversation_id(manager_id=manager_id)
                ),
                inputs=[manager, active_chat],
                outputs=active_chat,
            ).then(
                sidebar_ui.conversation_choices,
                inputs=[manager, active_chat],
                outputs=history_list,
            ).then(
                sidebar_ui.title_conversation,
                inputs=[manager, active_chat],
                outputs=history_list,
                concurrency_id="titles",
                concurrency_limit=1,
                show_progress="hidden",
            ).then(
                sidebar_ui.conversation_location,
                inputs=[manager, active_chat],
                outputs=chat_location,
            ).then(
                summary_ui.summarize_conversation,
                inputs=[manager, active_chat],
                concurrency_id="summaries",
                concurrency_limit=1,
                show_progress="hidden",
            ).then(
                summary_ui.summary_card,
                inputs=[manager, active_chat],
                outputs=summary_outputs,
                show_progress="hidden",
            )
        chatbot.change(
            fn=None,
            js="""(history) => [
                {__type__: 'update', visible: !(history && history.length)},
                {__type__: 'update', placeholder: history && history.length
                    ? 'Ask a follow-up…' : "What's on your mind?"}
            ]""",
            inputs=chatbot,
            outputs=[suggestions, message],
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
                const actions = latest.closest('.message-row')?.nextElementSibling;
                const messageBottom = actions?.classList.contains('message-buttons')
                    ? actions.getBoundingClientRect().bottom : bounds.bottom;
                const dock = document.querySelector('#composer-dock').getBoundingClientRect();
                const bottom = dock.top - 24;
                const top = 24;
                const target = messageBottom - bounds.top > bottom - top
                    ? scrollY + bounds.top - top
                    : scrollY + messageBottom - bottom;
                window.scrollTo({top: Math.max(0, target), behavior:
                    matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
            }""",
        )
        new_chat.click(
            chat_ui.clear_chat,
            inputs=manager,
            outputs=[chatbot, message, active_chat, workspace],
            queue=True,
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(
            sidebar_ui.conversation_choices,
            inputs=[manager, active_chat],
            outputs=history_list,
        ).then(
            summary_ui.summary_card,
            inputs=[manager, active_chat],
            outputs=summary_outputs,
        ).then(
            fn=None,
            js=sidebar_ui.CLOSE_SIDEBAR_ON_PHONE_JS,
        )
        pending_changes.change(
            chat_ui.pending_card,
            inputs=pending_changes,
            outputs=[pending_html, pending_box],
            queue=False,
            show_progress="hidden",
        )
        confirm_changes.click(
            chat_ui.confirm_pending,
            inputs=[pending_changes, chatbot, manager, active_chat],
            outputs=[chatbot, pending_changes],
            concurrency_id="workspace",
            concurrency_limit=1,
        ).then(
            settings.load_settings,
            inputs=manager,
            outputs=settings_form.outputs(),
            concurrency_id="settings",
            concurrency_limit=1,
        ).then(settings.load_summary, inputs=manager, outputs=settings_summary)
        cancel_changes.click(
            chat_ui.cancel_pending,
            inputs=[pending_changes, chatbot, manager, active_chat],
            outputs=[chatbot, pending_changes],
            concurrency_id="workspace",
            concurrency_limit=1,
        )
        # A proposal belongs to the chat it was made in.
        for event in (new_chat.click, history_list.input, load.click):
            event(list, outputs=pending_changes, queue=False)
    return app


def main() -> None:
    configure_logging()
    configure_uvicorn_logging()
    port = int(os.environ.get("PORT", "7860"))
    logger.info("DispatchDesk starting", host="0.0.0.0", port=port)
    app = build_app()
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
