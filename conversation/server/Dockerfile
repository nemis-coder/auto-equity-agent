# syntax=docker/dockerfile:1.7
# Servidor del agente conversacional: Aegra (compatible con LangGraph, Apache 2.0) y el grafo.
FROM python:3.12-slim
WORKDIR /app
COPY requirements/agent.lock requirements/agent.lock
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir --require-hashes -r requirements/agent.lock
COPY conversation/server/aegra.json conversation/server/auth.py conversation/server/graph.py conversation/server/test_graph.py ./
# Solo telemetría y transporte LLM compartidos; sin dominio ni persistencia del backend.
COPY app/__init__.py ./app/__init__.py
COPY app/observability ./app/observability
COPY app/providers/__init__.py app/providers/llm.py ./app/providers/
EXPOSE 2026
# Sin proveedor o sin clave el servicio arranca igual (la API y el asesor no lo necesitan), pero
# el log y el propio chat dicen qué variable falta.
CMD ["sh", "-c", "python -c 'import graph; graph.chat_settings()' || true; exec aegra serve --host 0.0.0.0 --port 2026 -c aegra.json"]
