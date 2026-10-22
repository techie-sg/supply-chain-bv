"""Verify real trace storage with synthetic content and no model/database calls."""

import json
import time
from typing import Any
from uuid import uuid4

from service.tracing import get_tracing_service, observation


def verify_tracing() -> dict[str, Any]:
    service = get_tracing_service()
    client = service._client
    if client is None:
        raise RuntimeError(
            "Configure and enable Langfuse before running tracing-check.",
        )
    if not client.auth_check():
        raise RuntimeError(
            "Langfuse authentication failed; check the project and region.",
        )
    marker = uuid4().hex
    session_id = f"diagnostic-{marker}"
    # Larger than the former cap, with a tail marker to detect content loss.
    content = "trace verification " + "0123456789" * 10_000 + f" end-{marker}"
    with observation(
        "dispatchdesk.tracing-check",
        input={"payload": content},
        metadata={
            "diagnostic": True,
            "provider_requests": 0,
            **{f"probe_{i}": i for i in range(200)},
        },
        user_id="diagnostic",
        session_id=session_id,
    ) as root:
        context = root.trace_context()
        if context is None:
            raise RuntimeError("Langfuse did not create a trace.")
        with observation("dispatchdesk.tracing-check.child", kind="tool") as child:
            child.update(output={"payload": content})
        root.update(output={"status": "complete", "provider_requests": 0})
    client.flush()
    status = service.export_status()
    if status.get("failed_spans", 0):
        raise RuntimeError("Langfuse export failed; see the export status in the logs.")
    for attempt in range(6):
        page = client.api.observations.get_many(
            trace_id=context["trace_id"],
            fields="core,basic,io,metadata",
            expand_metadata="details",
            limit=10,
            request_options={"timeout_in_seconds": 10, "max_retries": 0},
        )
        rows = {
            row.name: row for row in page.data if row.trace_id == context["trace_id"]
        }
        parent = rows.get("dispatchdesk.tracing-check")
        stored_child = rows.get("dispatchdesk.tracing-check.child")
        if parent is not None and stored_child is not None:
            if any(
                row.session_id != session_id or row.user_id != "diagnostic"
                for row in (parent, stored_child)
            ):
                raise RuntimeError(
                    "Stored trace identity does not match its diagnostic session.",
                )
            if stored_child.parent_observation_id != parent.id:
                raise RuntimeError("Stored trace hierarchy is incomplete.")
            metadata = parent.metadata or {}
            metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
            details = metadata.get("details")
            if details is None:
                # Some Langfuse deployments flatten nested metadata on readback.
                details = {
                    key.removeprefix("details."): value
                    for key, value in metadata.items()
                }
            details = json.loads(details) if isinstance(details, str) else details
            if any(details.get(f"probe_{i}") != i for i in range(200)):
                raise RuntimeError("Stored trace metadata is incomplete.")
            if service.capture_content:
                for value in (parent.input, stored_child.output):
                    decoded = json.loads(value) if isinstance(value, str) else value
                    if decoded != {"payload": content}:
                        raise RuntimeError("Stored trace content is incomplete.")
            elif any(
                value not in (None, "null", "")
                for value in (parent.input, parent.output, stored_child.output)
            ):
                raise RuntimeError("Content was stored despite metadata-only mode.")
            return {
                "trace_id": context["trace_id"],
                "stored_observations": 2,
                "full_content_verified": service.capture_content,
                "content_characters": len(content) if service.capture_content else 0,
                "provider_requests": 0,
                **status,
            }
        if attempt < 5:
            time.sleep(min(2**attempt, 4))
    raise RuntimeError(
        f"Langfuse accepted trace {context['trace_id']} but storage could not be verified.",
    )
