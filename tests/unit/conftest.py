"""SDK reales y HTTP simulado para ambos proveedores; sin red ni medición de calidad."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpx2
import pytest

from app.providers.llm import build_chat_model


@dataclass
class FakeLLM:
    route: str
    replies: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    def reply(self, payload: Any = None, *, text: str | None = None, refusal: bool = False,
              tokens: tuple[int, int] = (1200, 300)) -> FakeLLM:  # fmt: skip
        text = text if text is not None else json.dumps(payload)
        if self.route == "openai":
            part = ({"type": "refusal", "refusal": "No puedo leerlo."} if refusal else
                    {"type": "output_text", "text": text, "annotations": []})
            self.replies.append({
                "id": "resp_1", "object": "response", "created_at": 0,
                "model": "modelo-snapshot", "status": "completed",
                "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
                "output": [{"type": "message", "id": "msg_1", "role": "assistant",
                            "status": "completed", "content": [part]}],
                "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1],
                          "total_tokens": sum(tokens),
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}},
            })
            return self
        self.replies.append({
            "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
            "content": [] if refusal else [
                {"type": "text", "text": text}
            ],
            "stop_reason": "refusal" if refusal else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
        })
        return self

    def tool(self, name: str, args: dict) -> FakeLLM:
        self.reply(text="")
        if self.route == "openai":
            self.replies[-1]["output"] = [{"type": "function_call", "id": "fc_1",
                "call_id": "call_1", "name": name, "arguments": json.dumps(args),
                "status": "completed"}]
        else:
            self.replies[-1].update(stop_reason="tool_use", content=[{
                "type": "tool_use", "id": "call_1", "name": name, "input": args,
            }])
        return self

    def fail(self, status: int = 503) -> FakeLLM:
        self.replies.append(status)
        return self

    def handle(self, request):
        response = httpx.Response if self.route == "openai" else httpx2.Response
        self.urls.append(str(request.url))
        self.requests.append(json.loads(request.content))
        item = self.replies.pop(0)
        if isinstance(item, int):
            return response(
                item, json={"error": {"message": "no disponible", "type": "server_error"}}
            )
        if self.route == "openai":
            return self.response(response, item)
        blocks = item["content"]
        try:
            data = json.loads(blocks[0]["text"]) if blocks and "text" in blocks[0] else None
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            item = {**item, "stop_reason": ("max_tokens" if item["stop_reason"] == "max_tokens"
                                            else "tool_use"), "content": [{
                "type": "tool_use", "id": "toolu_1", "name": self.forced_tool(), "input": data
            }]}
        return self.response(response, item)

    def response(self, response, item):
        if not self.last.get("stream"):
            return response(200, json=item)
        events = self.stream_events(item)
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    def stream_events(self, item):
        if self.route == "openai":
            block = item["output"][0]
            start = {**block, "status": "in_progress"}
            if block["type"] == "function_call":
                start["arguments"] = ""
                updates = [{"type": "response.function_call_arguments.delta",
                            "delta": block["arguments"], "item_id": block["id"],
                            "output_index": 0}]
            else:
                start["content"] = []
                updates = [{"type": "response.output_text.delta", "output_index": 0,
                            "content_index": 0, "item_id": block["id"],
                            "delta": block["content"][0]["text"]},
                           {"type": "response.output_text.done", "output_index": 0,
                            "content_index": 0, "item_id": block["id"],
                            "text": block["content"][0]["text"]}]
            events = [
                {"type": "response.created", "response": {**item, "status": "in_progress",
                                                          "output": [], "usage": None}},
                {"type": "response.output_item.added", "output_index": 0, "item": start},
                *updates,
                {"type": "response.output_item.done", "output_index": 0, "item": block},
                {"type": "response.completed", "response": item},
            ]
            return [{**event, "sequence_number": i} for i, event in enumerate(events)]
        block = item["content"][0]
        if block["type"] == "tool_use":
            start = {**block, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        else:
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        return [
            {"type": "message_start", "message": {**item, "content": [], "stop_reason": None,
                "usage": {"input_tokens": item["usage"]["input_tokens"], "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": start},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": item["stop_reason"],
                "stop_sequence": None}, "usage": item["usage"]},
            {"type": "message_stop"},
        ]

    @property
    def last(self) -> dict[str, Any]:
        return self.requests[-1]

    def system(self) -> str:
        if self.route == "openai":
            return next(m["content"] for m in self.last["input"] if m.get("role") == "system")
        value = self.last["system"]
        return value if isinstance(value, str) else value[0]["text"]

    def image_count(self) -> int:
        key = "input" if self.route == "openai" else "messages"
        parts = next(m["content"] for m in self.last[key] if m.get("role") == "user")
        return sum(p.get("type") in {"image", "input_image"} for p in parts)

    def schema(self) -> dict[str, Any]:
        if self.route == "openai":
            return self.last["text"]["format"]["schema"]
        return self.last["tools"][0]["input_schema"]

    def forced_tool(self) -> str | None:
        choice = self.last.get("tool_choice", {})
        return choice.get("name") if choice.get("type") == "tool" else None


@pytest.fixture(params=["anthropic", "openai"])
def llm(monkeypatch, request):
    import langchain_anthropic.chat_models as anthropic_models
    import langchain_openai

    fake = FakeLLM(request.param)
    original_openai = langchain_openai.ChatOpenAI
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **kw: original_openai(
        **kw, http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    ))
    def make(model: str = "modelo-snapshot", max_tokens: int = 600):
        # Cada construcción conserva la ruta pero inicia un intercambio independiente.
        fake.replies.clear()
        fake.requests.clear()
        fake.urls.clear()
        monkeypatch.setattr(
            anthropic_models,
            "_get_default_async_httpx_client",
            lambda **kw: httpx2.AsyncClient(
                transport=httpx2.MockTransport(fake.handle), base_url=kw["base_url"]
            ),
        )
        chat = build_chat_model(
            fake.route, model, max_output_tokens=max_tokens, api_key="test-provider-key"
        )
        return chat, fake

    return make
