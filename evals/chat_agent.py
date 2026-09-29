"""El agente de la conversación dentro del proceso, para la evaluación y las pruebas.

Ejecuta **el mismo grafo** que sirve Aegra (`conversation/server/graph.py`) contra la API de la
evaluación, con un checkpointer en memoria y un modelo guionado: cada paso del guion es lo que
«respondería» el modelo (texto o una llamada a herramienta). Así se prueban la orquestación y los
controles del agente visible (tarjetas, herramientas permitidas, presupuesto y escalamiento) sin
red ni costo. La calidad de un modelo real se mide aparte.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

GRAPH_PATH = Path(__file__).resolve().parents[1] / "conversation" / "server" / "graph.py"
MODULE = "auto_equity_chat_graph"
SCRIPTED = ("scripted", "scripted-chat-v1")


def load_graph() -> Any:
    if MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(MODULE, GRAPH_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE] = module
        spec.loader.exec_module(module)
    return sys.modules[MODULE]


def call(name: str, **args: Any) -> dict[str, Any]:
    """Un paso del guion: el modelo pide esta herramienta."""
    return {"name": name, "args": args, "id": f"call-{uuid.uuid4().hex[:10]}"}


class ScriptedChatModel:
    """Devuelve los pasos del guion en orden; sin pasos, cierra con una frase neutra."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps = list(steps)
        self.calls = 0

    async def ainvoke(self, messages: list, config=None) -> AIMessage:
        self.calls += 1
        step = self.steps.pop(0) if self.steps else "¿Te ayudo con algo más?"
        reply = AIMessage(step) if isinstance(step, str) else AIMessage("", tool_calls=[step])
        size = sum(len(str(m.content)) for m in messages) // 4
        reply.usage_metadata = {"input_tokens": size, "output_tokens": 40,
                                "total_tokens": size + 40}  # fmt: skip
        return reply


class TestClientTransport(httpx.AsyncBaseTransport):
    """Lleva las llamadas HTTP del grafo al `TestClient` de la evaluación (su propio loop)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        response = await asyncio.to_thread(
            self.client.request,
            request.method,
            request.url.raw_path.decode(),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=body,
        )
        return httpx.Response(response.status_code, headers=response.headers,
                              content=response.content)  # fmt: skip


class ChatSession:
    """Un hilo de conversación de un cliente, como lo abriría Agent Chat UI."""

    def __init__(self, client: Any, token: str) -> None:
        self.graph = load_graph()
        self.client = client
        self.app = self.graph.builder.compile(checkpointer=InMemorySaver())
        self.config = {
            "configurable": {
                "thread_id": str(uuid.uuid4()),
                "langgraph_auth_user": {"api_token": token},
            }
        }
        self.model: ScriptedChatModel | None = None

    def _run(self, payload: Any, script: list[Any]) -> dict[str, Any]:
        g = self.graph
        self.model = ScriptedChatModel(script)
        saved = (g._llm, g._llm_meta, g.HTTP_TRANSPORT)
        g._llm, g._llm_meta = self.model, SCRIPTED
        g.HTTP_TRANSPORT = TestClientTransport(self.client)
        try:
            return asyncio.run(self.app.ainvoke(payload, self.config))
        finally:
            g._llm, g._llm_meta, g.HTTP_TRANSPORT = saved

    def say(self, text: str, script: list[Any], case_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": [HumanMessage(text)]}
        if case_id:
            payload["case_id"] = case_id
        return self._run(payload, script)

    def decide(self, approve: bool, script: list[Any]) -> dict[str, Any]:
        decision = {"type": "approve"} if approve else {"type": "reject", "message": "No."}
        return self._run(Command(resume={"decisions": [decision]}), script)

    def waiting_for_card(self) -> bool:
        return bool(self.app.get_state(self.config).interrupts)

    @staticmethod
    def results(state: dict[str, Any], tool: str) -> list[Any]:
        """Lo que el agente recibió de una herramienta (lo que puede decirle al cliente)."""
        out = []
        for m in state["messages"]:
            if isinstance(m, ToolMessage) and m.name == tool:
                try:
                    out.append(json.loads(m.content))
                except ValueError:
                    out.append(m.content)
        return out
