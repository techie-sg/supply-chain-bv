import gradio as gr

from dispatchdesk.rag import answer_question


def chat(
    message: str,
    history: list[dict] | None,
) -> tuple[list[dict], str]:
    """Handle a user message and return updated chat history."""

    history = history or []

    if not message.strip():
        return history, ""

    answer = answer_question(message)

    updated_history = history + [
        {
            "role": "user",
            "content": message,
        },
        {
            "role": "assistant",
            "content": answer,
        },
    ]

    return updated_history, ""


def build_app() -> gr.Blocks:
    """Build the DispatchDesk Gradio application."""

    with gr.Blocks(title="DispatchDesk") as app:

        gr.Markdown(
            """
            # DispatchDesk

            **Dispatch manager decision-support assistant**

            Ask about dispatch delays, weather impacts, batching,
            rider safety, or operational procedures.
            """
        )

        chatbot = gr.Chatbot(
            label="DispatchDesk",
            height=500,
        )

        message = gr.Textbox(
            label="Message",
            placeholder="Why are my deliveries slipping when it rains?",
        )

        submit = gr.Button("Ask")

        submit.click(
            fn=chat,
            inputs=[message, chatbot],
            outputs=[chatbot, message],
        )

        message.submit(
            fn=chat,
            inputs=[message, chatbot],
            outputs=[chatbot, message],
        )

    return app


app = build_app()


if __name__ == "__main__":
    app.launch()