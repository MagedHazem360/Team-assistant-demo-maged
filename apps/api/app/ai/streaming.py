"""Streamed answers as Server-Sent Events (ADR-0009; frame shapes ADR-0015).

Wire format (one ``event:`` + one JSON ``data:`` line per event, blank-line terminated):

    event: sources  data: {"sources": [{"title": "T", "path": "a.md"}], "count": 3}
    event: token    data: {"text": "partial "}
    event: done     data: {"sources": [...], "input_tokens": 12, "output_tokens": 40}
    event: error    data: {"error": "ai_unavailable", "error_kind": "timeout"}

``sources`` comes after retrieval, ``token`` per model chunk; after ``error`` the stream ends.
``error_kind`` is a bounded classification (``timeout``, ``auth``, ``network``, …) — never the
exception message, which may echo content.

Use from a ``/v1`` route::

    @router.post("/chat/stream")
    async def chat_stream(body: ChatIn) -> StreamingResponse:
        graph = build_graph()
        return sse_response(stream_answer(graph, body.question))

``on_complete`` runs after the last token and **before** ``done`` (so ``done`` means "stored"); a
client that disconnects earlier closes the generator and ``on_complete`` never runs. ``timeout``
bounds the whole run (retrieval + model). The BFF forwards the stream with the streaming
pass-through helper (``apps/web/src/lib/stream.ts``), not ``fetchUpstream``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, AnyMessage, HumanMessage

from app.ai.graph import Citation, GraphState, _message_text, unique_citations
from app.ai.telemetry import error_kind, extract_usage

SSE_MEDIA_TYPE = "text/event-stream"
ERROR_CODE = "ai_unavailable"


@dataclass
class StreamResult:
    """What a finished stream produced — handed to ``on_complete`` (never logged)."""

    text: str
    citations: list[Citation] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None


def sse(event: str, data: dict[str, Any]) -> str:
    """Format one SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def error_frame(exc: BaseException) -> str:
    return sse("error", {"error": ERROR_CODE, "error_kind": error_kind(exc)})


async def stream_answer(
    graph: Any,
    question: str,
    *,
    history: list[AnyMessage] | None = None,
    config: dict[str, Any] | None = None,
    timeout: float | None = None,
    on_complete: Callable[[StreamResult], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """Run the graph and yield SSE frames: ``sources`` → ``token``* → ``done`` (or ``error``)."""
    inputs: GraphState = {
        "messages": [*(history or []), HumanMessage(content=question)],
        "context": [],
    }
    result = StreamResult(text="")
    parts: list[str] = []
    final_text = ""  # the answer node's complete message — the fallback when nothing streamed
    try:
        async with asyncio.timeout(timeout):
            async for mode, payload in graph.astream(
                inputs, config, stream_mode=["updates", "messages"]
            ):
                if mode == "updates":
                    for node, update in (payload or {}).items():
                        if node == "retrieve":
                            context = (update or {}).get("context", []) or []
                            result.citations = unique_citations(context)
                            yield sse(
                                "sources", {"sources": result.citations, "count": len(context)}
                            )
                        elif node == "answer":
                            for message in (update or {}).get("messages", []) or []:
                                i, o = extract_usage(message)
                                result.input_tokens = i if i is not None else result.input_tokens
                                result.output_tokens = o if o is not None else result.output_tokens
                                final_text = _message_text(message) or final_text
                elif mode == "messages":
                    chunk, metadata = payload
                    if (
                        isinstance(chunk, AIMessageChunk)
                        and (metadata or {}).get("langgraph_node") == "answer"
                        and not getattr(chunk, "tool_call_chunks", None)
                    ):
                        text = _message_text(chunk)
                        if text:
                            parts.append(text)
                            yield sse("token", {"text": text})
        if not parts and final_text:
            # The model returned its answer in one piece (it did not stream): still deliver it
            # as a token frame, and never store an empty answer.
            parts.append(final_text)
            yield sse("token", {"text": final_text})
        result.text = "".join(parts)
        if on_complete is not None:
            await on_complete(result)
    except Exception as exc:  # noqa: BLE001 - the frame carries a bounded kind, never the message
        yield error_frame(exc)
        return
    yield sse(
        "done",
        {
            "sources": result.citations,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    )


def sse_response(frames: AsyncIterator[str]) -> StreamingResponse:
    """A FastAPI response that streams SSE frames without proxy buffering."""
    return StreamingResponse(
        frames,
        media_type=SSE_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
