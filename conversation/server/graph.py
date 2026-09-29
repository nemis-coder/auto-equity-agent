"""Grafo `auto_equity`: el recorrido completo de Auto Equity con Agent Chat UI sobre Aegra.

El grafo es un cliente más de la API de Auto Equity: sus herramientas llaman a los endpoints
con el token del cliente, así que autenticación, allowlist, `If-Match`, idempotencia, límites y
todas las reglas de negocio siguen en FastAPI.

Lo que el modelo NO puede hacer solo: guardar datos o elegir una oferta. Esas herramientas
preparan una propuesta y el grafo se pausa con `interrupt()`; Agent Chat UI muestra una tarjeta
y solo la decisión de la persona hace que el código llame a `/confirmations` o `/selections`.

Reglas de Agent Chat UI que el grafo respeta:
- la tarjeta solo se dibuja si el último mensaje del hilo es del asistente: la pausa ocurre
  justo después del mensaje con la llamada a la herramienta, antes de su resultado;
- una herramienta por turno (`parallel_tool_calls=False`) para cumplir lo anterior.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any

import httpx
from anthropic import APIError as AnthropicAPIError
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.constants import TAG_NOSTREAM
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from openai import APIError as OpenAIAPIError

from app.observability.agent import conversation_context, get_tracer, observe_node
from app.observability.context import trace_headers
from app.observability.costs import token_usage
from app.providers.llm import build_chat_model, is_incomplete, is_refusal

API = os.environ.get("AUTO_EQUITY_API_URL", "http://api:8000")
# La evaluación ejecuta el grafo contra la API en el mismo proceso (`httpx.ASGITransport`).
HTTP_TRANSPORT: Any = None
# Se conserva el modelo Anthropic existente; OpenAI requiere CHAT_MODEL explícito (0043).
CHAT_MODEL = "claude-sonnet-5"

PROMPT = """Eres el asistente de Auto Equity. Ayudas a una persona a solicitar un crédito usando
su auto como garantía; el auto sigue siendo suyo y lo sigue usando. Hablas en español, cálido,
claro y breve. Haz UNA pregunta a la vez (el domicilio puede pedirse junto).

Lenguaje para el cliente:
- Nunca muestres códigos de estado, nombres de herramientas, campos internos ni JSON, tampoco
  entre paréntesis ni aunque la persona los pida. Son información interna, no parte del diálogo.
  Explica el avance en español: «necesitamos corregir un documento», «un asesor debe revisar
  tu solicitud» o «tu expediente está listo para revisión de la financiera».
- Pide marca, modelo, año y placa por separado, solo lo que falte. Pide primero el ingreso
  neto y después su periodicidad si no la indicó. Si da varios datos espontáneamente,
  consérvalos y no vuelvas a preguntarlos. No obligues a repetirlos uno por uno.
- No prometas llamadas, mensajes, contacto, envío automático a una financiera ni plazos:
  esta demo prepara el expediente, no realiza esas acciones. Tampoco prometas aprobación
  ni desembolso. Un asesor pendiente de revisión no significa que ya lo esté atendiendo.
- Al cerrar, sé breve y sobrio, sin celebraciones ni emojis que sugieran crédito aprobado.

Si la persona apenas saluda, preséntate en una frase y empieza por la primera pregunta.
La validez de los datos la decide la API, no tú. Al proponer, la API revisa el formato y
te responde `datos_invalidos` si algo falla: explica cada problema y vuelve a preguntar esos
datos. Fuera de eso, acepta lo que la persona dice aunque te parezca raro: no cuestiones si un
código postal, una colonia o un nombre existen (por ejemplo, 00000 es un código postal válido
aquí). Solo pregunta de nuevo si falta un dato o la respuesta no se entiende.

El proceso tiene cuatro etapas y la API decide en cuál estás; usa `consultar_solicitud` para
saberlo antes de preguntar lo que ya está y después de cada paso importante. Si la persona
pregunta qué falta, en qué va o dice que ya hizo algo, llama a `consultar_solicitud` ANTES de
responder: no contestes de memoria.

1. Tu auto: si está a su nombre, si tiene adeudos o gravámenes que impidan usarlo como garantía,
   si tiene la segunda llave, y marca, modelo, año y placa (o número de serie). Luego llama a
   `proponer_datos_del_auto`.
   - Si no está a su nombre o tiene un adeudo que impide usarlo, NO decidas tú: llama a
     `proponer_datos_del_auto` de inmediato con las respuestas que tienes; la persona las
     confirma y el sistema registra el resultado. Después explica el motivo con amabilidad.
   - Sin segunda llave NO es un rechazo: se cotiza su reposición y se suma una vez al plan.
2. Tu perfil: nombre completo, domicilio (calle, número exterior, número interior si hay,
   colonia, municipio o alcaldía, estado, código postal), si es asalariado o independiente,
   empleador o actividad, ingreso NETO (después de impuestos) y cada cuánto lo recibe. Si no dice
   que es neto, pregúntalo. Luego llama a `proponer_datos_personales`. NO preguntes cuánto
   dinero quiere: los montos los ofrece la financiera en las opciones; si la persona menciona un
   monto, explícale que verá las opciones disponibles y elegirá una.
3. Opciones: con `consultar_solicitud` verás las opciones que dio el cotizador de la financiera
   (montos y plazos). Preséntalas TODAS con sus cifras EXACTAS (efectivo, plazo, cuota, último
   pago, costo de llave, total) y pregunta cuál prefiere. Cuando la persona elija, llama a
   `preparar_eleccion` con el plazo y el efectivo de esa opción. Nunca elijas por ella ni
   recomiendes una. No inventes opciones ni montos que no estén en la lista.
   Usa una tabla Markdown con columnas Efectivo, Meses, Cuota, Último pago, Llave y Total;
   indica que los importes son MXN. No ocultes costos ni elimines opciones para abreviar.
4. Documentos: pide que adjunte con «Upload PDF or Image» su identificación oficial, su
   comprobante de ingresos (recibo de nómina si es asalariada, estado de cuenta si es
   independiente) y la tarjeta de circulación o factura del auto. Cuando llegue un adjunto, llama
   a `subir_documento` con el tipo que corresponde y el nombre exacto del archivo, uno por uno.
   Informa el resultado. Si la API pide una corrección, explica qué documento y qué dato, y pide
   el reemplazo.

Correcciones (etapa NEEDS_CORRECTION): `siguiente_paso.reasons` dice qué regla falló y en qué
documento o dato. Si el documento no coincide con un dato que la persona DECLARÓ (nombre,
domicilio, ingreso), pregunta cuál es el correcto: si el error estaba en lo declarado, vuelve a
llamar a `proponer_datos_personales` con el dato corregido; si el documento es el equivocado o
está borroso, pide un reemplazo. Subir el mismo archivo otra vez no corrige nada.
Para ingresos, consulta `ingreso_declarado`: conserva monto NETO, moneda y periodicidad.
No exijas que un recibo quincenal muestre el ingreso mensual: 10,000 netos por quincena
pueden respaldar 20,000 netos al mes; la conversión y validación las hace la API, no tú.
No confundas quincenal (dos veces al mes) con cada 14 días. Pregunta si debe corregirse la
declaración o reemplazarse el documento; no pidas «solo números» sin aclarar el período.
Si mantiene su declaración y pide reemplazar el documento, solicita el archivo correcto
sin volver a proponer el mismo ingreso ni exigir que coincida el importe de períodos distintos.

Resultados: interpreta estos códigos de `etapa` INTERNAMENTE; di solo su explicación:
- READY_FOR_FINANCIAL: listo para revisión de la financiera. NO es un crédito aprobado ni un
  desembolso.
- HUMAN_REVIEW: un asesor revisará la solicitud; NO está resuelta ni aprobada. Explícalo sin
  alarmar y sin decir que algo se resolvió.
- REJECTED: no se puede continuar; explica el motivo con amabilidad.
READY_FOR_FINANCIAL y REJECTED son finales: la solicitud ya no admite cambios, documentos nuevos
ni reemplazos. No los ofrezcas; si la persona quiere cambiar algo, explica que esta solicitud
está cerrada y puede iniciar una conversación nueva para otra solicitud.
Cerrar cambios NO cierra las consultas. Para preguntas sobre el monto, plazo, cuotas,
costos o resumen del proceso, llama a `consultar_solicitud` y usa `oferta_elegida`, nunca
otra oferta ni el recuerdo del chat. Una selección vencida es un registro histórico, no una
nueva oferta vigente. Si falta su detalle, dilo; no lo reconstruyas ni inventes cifras.
Ejemplo de cierre: «Ana, tu expediente está completo y listo para revisión de la financiera.
Esto todavía no significa que el crédito esté aprobado ni que se haya realizado un desembolso».
Si un servicio falla, ofrece `reintentar`.
Si la persona pide hablar con un asesor o con una persona, llama a `pedir_asesor` sin
insistir ni preguntar el motivo, y explica que un asesor revisará su solicitud (no es un
rechazo).

Reglas:
- Las herramientas de proponer y elegir NO guardan nada: la persona verá una tarjeta para
  aprobar. Nunca digas que algo quedó guardado o elegido antes de que lo apruebe.
- El resultado de esas herramientas llega DESPUÉS de que la persona decide: `aprobado: true`
  significa que ya quedó guardado o elegido (dilo así y sigue con el siguiente paso);
  `aprobado: false` significa que lo rechazó (pregunta qué corregir). Nunca digas que una
  tarjeta sigue pendiente cuando ya tienes su resultado.
- Acepta los datos tal como la persona los da; no pidas precisiones que no son necesarias (por
  ejemplo, «Ciudad de México» sirve como municipio y como estado). No repitas preguntas.
- Copia cada dato EXACTAMENTE como lo dijo la persona, sin quitar ni cambiar palabras: si dice
  «Colonia Ejemplo», la colonia es «Colonia Ejemplo», no «Ejemplo». Se compara con sus
  documentos letra por letra.
- Tú no puedes confirmar, aprobar ni elegir por la persona.
- Un «sí» o «no» a una pregunta de sí o no es una respuesta clara: acéptala a la primera y no
  la vuelvas a preguntar. Pide confirmación solo si es realmente ambigua («creo que sí», «no sé»).
- Nunca llames a proponer_datos_del_auto con argumentos vacíos. «No está a mi nombre» se
  traduce en owned_by_customer=false; «no tiene adeudos», en blocking_debt=false. False es un
  dato conocido, no un campo ausente. Si el auto puede continuar, espera los SIETE datos antes
  de proponer. Si una propuesta está incompleta, recupera lo ya dicho del historial y pregunta
  solo lo que realmente no se haya respondido.
- Haz preguntas de sí o no directas; nunca preguntas de «¿esto o aquello?», que no se pueden
  contestar con un sí.
- Tú nunca decides si alguien califica: eso lo decide el sistema con las respuestas confirmadas.
- No inventes cifras: usa solo las que devuelven las herramientas.
- No hables de otros temas."""

READABLE = {
    "owned_by_customer": "¿El auto está a tu nombre?",
    "blocking_debt": "¿Tiene adeudos que impidan usarlo como garantía?",
    "has_second_key": "¿Tienes la segunda llave?",
    "make": "Marca",
    "model": "Modelo",
    "year": "Año",
    "vehicle_ref": "Placa o número de serie",
    "full_name": "Nombre completo",
    "address": "Domicilio",
    "employment": "Situación laboral",
    "employer_or_activity": "Empleador o actividad",
    "income": "Ingreso neto",
}
EMPLOYMENT = {"SALARIED": "Asalariado", "SELF_EMPLOYED": "Independiente"}
PERIODS = {
    "MONTHLY": "mensual",
    "SEMIMONTHLY": "quincenal",
    "BIWEEKLY_14D": "cada 14 días",
    "WEEKLY": "semanal",
}
SLOTS = {
    "IDENTITY": "IDENTITY",
    "PAYSLIP": "INCOME",
    "INCOME_STATEMENT": "INCOME",
    "VEHICLE_OWNERSHIP": "VEHICLE_OWNERSHIP",
}
APPROVAL_TOOLS = {"proponer_datos_del_auto", "proponer_datos_personales", "preparar_eleccion"}


class State(MessagesState):
    case_id: str | None
    pending: dict[str, Any] | None
    turn_failed: bool
    model_route: dict[str, str]  # evita cambiar proveedor/modelo en medio de un hilo
    capture_errors: int  # reparación acotada por mensaje de la persona


# --- Esquemas de herramientas (la ejecución vive en los nodos) --------------------------------


@tool
def consultar_solicitud() -> str:
    """Consulta incluso tras el cierre: estado, oferta elegida con costos, ingreso declarado
    y periodicidad, documentos, correcciones y resultado. No cambia el expediente."""
    return ""


@tool
def proponer_datos_del_auto(
    owned_by_customer: bool | None = None,
    blocking_debt: bool | None = None,
    has_second_key: bool | None = None,
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
    vehicle_ref: str | None = None,
) -> str:
    """Propone las respuestas del auto. La persona las aprueba en una tarjeta; no guarda nada
    solo. Si una respuesta impide continuar (no está a su nombre, o tiene un adeudo que impide
    usarlo), llámala de inmediato solo con las respuestas que tengas: el sistema decide. Si el
    auto califica, incluye las tres respuestas y marca, modelo, año y placa."""
    return ""


@tool
def proponer_datos_personales(
    full_name: str,
    street: str,
    external_number: str,
    neighborhood: str,
    municipality: str,
    state: str,
    postal_code: str,
    employment: str,
    employer_or_activity: str,
    net_income_amount: str,
    income_period: str,
    internal_number: str | None = None,
) -> str:
    """Propone el perfil. La persona lo aprueba en una tarjeta; no guarda nada solo.

    Domicilio en partes, copiadas tal cual: street es SOLO el nombre de la calle, sin número
    («Calle Demo»); external_number es el número exterior («123»); internal_number solo si hay;
    neighborhood completo («Colonia Ejemplo»). employment: SALARIED o SELF_EMPLOYED.
    income_period: MONTHLY, SEMIMONTHLY, BIWEEKLY_14D o WEEKLY. Montos en MXN solo con números
    (ej. 20000). El ingreso es NETO."""
    return ""


@tool
def preparar_eleccion(plazo_meses: int, efectivo: str) -> str:
    """Prepara la opción que la persona eligió de la lista del cotizador: su plazo en meses y
    el efectivo que recibe (solo números, ej. 50000). Ella la elige en una tarjeta."""
    return ""


@tool
def subir_documento(tipo: str, archivo: str) -> str:
    """Sube un archivo que la persona adjuntó. tipo: IDENTITY (identificación oficial), PAYSLIP
    (recibo de nómina), INCOME_STATEMENT (estado de cuenta) o VEHICLE_OWNERSHIP (tarjeta de
    circulación o factura). archivo: nombre exacto del adjunto."""
    return ""


@tool
def reintentar() -> str:
    """Reintenta el paso pendiente cuando un servicio externo no respondió."""
    return ""


@tool
def pedir_asesor() -> str:
    """Pasa la solicitud a un asesor humano. Úsala cuando la persona pida hablar con una
    persona o un asesor. No es un rechazo: la solicitud queda en revisión."""
    return ""


TOOLS = [
    consultar_solicitud,
    proponer_datos_del_auto,
    proponer_datos_personales,
    preparar_eleccion,
    subir_documento,
    reintentar,
    pedir_asesor,
]

# --- API de Auto Equity --------------------------------------------------------------------


class APIUnavailable(Exception):
    """No conocemos el resultado: nunca anunciar una escritura como confirmada."""


API_UNAVAILABLE = (
    "No pude confirmar el resultado de este paso. Los datos que ya habías confirmado se "
    "conservan. Intenta nuevamente en unos minutos; revisaremos el estado antes de continuar."
)


def recover_api_errors(fn):
    """Cierra el turno sin llamar al modelo; no captura fallos de código ni interrupt()."""
    @wraps(fn)
    async def recovered(state, config):
        try:
            return await fn(state, config)
        except APIUnavailable:
            calls = getattr(state["messages"][-1], "tool_calls", [])
            ids = [c["id"] for c in calls]
            pending = state.get("pending")
            if pending and pending.get("tool_call_id") not in ids:
                ids.append(pending["tool_call_id"])
            messages = [ToolMessage(json.dumps({"error": "API_UNAVAILABLE"}), tool_call_id=i)
                        for i in ids]
            messages.append(AIMessage(API_UNAVAILABLE))
            # Una escritura incierta exige consultar el estado y, si falta, nueva aprobación.
            return {"messages": messages, "pending": None, "turn_failed": True}
    return recovered


def _token(config: RunnableConfig) -> str:
    # Aegra 0.10.5 entrega un modelo `User` (campos extra como atributos), no un dict como
    # dice su documentación; se aceptan ambas formas.
    user = config["configurable"]["langgraph_auth_user"]
    return user["api_token"] if isinstance(user, dict) else user.api_token


async def _api(
    config: RunnableConfig,
    method: str,
    path: str,
    *,
    version: int | None = None,
    body: Any = None,
    files: Any = None,
    data: Any = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {_token(config)}"}
    if method != "GET":
        headers["Idempotency-Key"] = f"chat-{uuid.uuid4()}"
    if version is not None:
        headers["If-Match"] = str(version)
    # También cubre confirmaciones tras interrupt(), sin cronometrar la espera humana.
    context = None if trace_headers() else conversation_context(config)
    with get_tracer().span("agent.api", trace_context=context) as span:
        headers.update(trace_headers())
        async with httpx.AsyncClient(base_url=API, timeout=120, transport=HTTP_TRANSPORT) as client:
            # Solo un reintento de transporte, con la MISMA clave y versión. Si se perdió
            # la respuesta de un comando aplicado, la API devuelve el resultado persistido.
            for attempt in range(2):
                try:
                    response = await client.request(
                        method, path, headers=headers, json=body, files=files, data=data
                    )
                    break
                except httpx.RequestError as exc:
                    if attempt:
                        raise APIUnavailable() from exc
                    await asyncio.sleep(0.1)
        span.update(metadata={"http_status": response.status_code})
        if response.status_code >= 400:
            span.update(level="ERROR")
        if response.status_code >= 500 or response.status_code in (401, 403, 429):
            raise APIUnavailable()
    try:
        body = response.json()
    except ValueError as exc:
        raise APIUnavailable() from exc
    if not isinstance(body, dict):
        raise APIUnavailable()
    return response.status_code, body


async def _snapshot(config: RunnableConfig, case_id: str) -> dict[str, Any]:
    status, body = await _api(config, "GET", f"/cases/{case_id}")
    if status != 200:
        raise APIUnavailable()
    return body


def _offer_view(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "plazo_meses": offer["term_months"],
        "cuota_mensual": offer["regular_payment"],
        "ultimo_pago": offer["last_payment"],
        "efectivo": offer["cash_amount"],
        "costo_llave": offer["key_cost"],
        "capital_financiado": offer["financed_principal"],
        "tasa_anual": offer["annual_nominal_rate"],
        "total": offer["total_payment"],
    }


def _selected_offer(snap: dict[str, Any]) -> dict[str, Any] | None:
    """Resuelve solo la selección del snapshot autorizado, incluso como consulta histórica."""
    offer_id = (snap.get("selection") or {}).get("offer_id")
    if not offer_id:
        return None
    return next((o for o in snap.get("offers", []) if o.get("offer_id") == offer_id), None)


def _summary(s: dict[str, Any]) -> dict[str, Any]:
    """Lo que el modelo necesita de la solicitud (sin tokens)."""
    selected = _selected_offer(s)
    return {
        "etapa": s["workflow_state"],
        "siguiente_paso": s["next_action"],
        "espera": s.get("wait_reason"),
        "auto": s["vehicle"],
        "faltan": s["missing_fields"],
        "ofertas": [_offer_view(o) for o in s.get("offers", []) if not o.get("expired")],
        "eleccion": s.get("selection"),
        "oferta_elegida": None if selected is None else {
            **_offer_view(selected), "vencida": bool(selected.get("expired")),
        },
        "ingreso_declarado": (s.get("profile") or {}).get("income"),
        "documentos": [
            {"tipo": d["kind"], "estado": d["status"], "version": d["revision"]}
            for d in s.get("documents", [])
        ],
        "rondas_de_correccion": s.get("correction_rounds"),
        "rechazo": (s.get("eligibility") or {}).get("rejection_reasons"),
        "propuesta_pendiente": bool(s.get("pending_action")),
    }


# --- Nodos -------------------------------------------------------------------------------


@recover_api_errors
@observe_node("agent.ensure_case")
async def ensure_case(state: State, config: RunnableConfig) -> dict[str, Any]:
    """Cada hilo trabaja sobre una solicitud; «Nueva solicitud» reutiliza una sin empezar."""
    if state.get("case_id"):
        return {"turn_failed": False, "capture_errors": 0}
    status, body = await _api(config, "POST", "/cases")
    if status not in (200, 201):
        raise APIUnavailable()
    return {"case_id": body["case_id"], "pending": None, "turn_failed": False, "capture_errors": 0}


async def reshow(state: State) -> dict[str, Any]:
    """Con una tarjeta sin decidir, un mensaje nuevo se retira para que la tarjeta vuelva a
    quedar anclada al último mensaje del asistente (y sin llamar al modelo)."""
    last = state["messages"][-1]
    if isinstance(last, HumanMessage) and last.id:
        return {"messages": [RemoveMessage(id=last.id)]}
    return {}


def _block_name(block: dict[str, Any]) -> str:
    meta = block.get("metadata") or {}
    return meta.get("filename") or meta.get("name") or block.get("filename") or ""


def _for_model(messages: list) -> list:
    """Vista del hilo para el modelo.

    - Los adjuntos no se reenvían (costo y privacidad): los lee la API.
    - Cada resultado de herramienta debe seguir a su llamada, o el proveedor responde 400.
      Agent Chat UI agrega resultados «do-not-render» para llamadas que ve en pantalla; si el
      hilo se bifurcó, esa llamada no está en esta rama y el resultado queda huérfano: se omite.
      Una llamada sin resultado recibe uno neutro.
    """
    out: list = []
    open_calls: dict[str, str] = {}  # id → nombre de las llamadas sin resultado

    def close_open_calls() -> None:
        for call_id, name in open_calls.items():
            out.append(ToolMessage(content="Sin resultado.", tool_call_id=call_id, name=name))
        open_calls.clear()

    for m in messages:
        if isinstance(m, ToolMessage):
            if m.tool_call_id in open_calls:
                open_calls.pop(m.tool_call_id)
                out.append(m)
            continue
        close_open_calls()
        if isinstance(m, AIMessage) and m.tool_calls:
            open_calls = {c["id"]: c["name"] for c in m.tool_calls}
        if isinstance(m, HumanMessage) and isinstance(m.content, list):
            parts: list[dict[str, Any]] = []
            for block in m.content:
                if isinstance(block, dict) and block.get("type") in ("image", "file"):
                    parts.append({"type": "text", "text": f"[Adjunto: {_block_name(block)}]"})
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})
                elif isinstance(block, str):
                    parts.append({"type": "text", "text": block})
            m = HumanMessage(content=parts or "[mensaje vacío]", id=m.id)
        out.append(m)
    close_open_calls()
    return _window(out)


# Historial que ve el modelo: con el prompt y las herramientas (~4k tokens) cabe en la reserva
# de 16k tokens por llamada. El estado vigente no se pierde: `consultar_solicitud` lo trae.
MAX_HISTORY_CHARS = 48_000


def _size(m: Any) -> int:
    content = m.content if isinstance(m.content, str) else json.dumps(m.content)
    calls = json.dumps(getattr(m, "tool_calls", None) or [], ensure_ascii=False)
    return len(content) + len(calls)


def _window(messages: list) -> list:
    """Quita los mensajes más antiguos hasta caber, cortando solo antes de un mensaje de la
    persona: así nunca se separa una llamada a herramienta de su resultado."""
    total = sum(_size(m) for m in messages)
    start = 0
    while total > MAX_HISTORY_CHARS:
        cut = next(
            (i for i in range(start + 1, len(messages)) if isinstance(messages[i], HumanMessage)),
            None,
        )
        if cut is None:
            break  # un solo intercambio más largo que el límite: se envía completo
        total -= sum(_size(m) for m in messages[start:cut])
        start = cut
    return messages[start:]


def chat_settings(env: dict[str, str] | None = None) -> tuple[str, str, str]:
    """Proveedor, modelo y clave de la conversación, tomados de las variables de entorno.

    La clave es obligatoria: sin ella la conversación no puede funcionar y el error lo dice.
    """
    env = dict(os.environ if env is None else env)
    provider = (env.get("CHAT_PROVIDER") or "").strip().lower()
    if provider not in {"anthropic", "openai"}:
        raise RuntimeError(
            "Define CHAT_PROVIDER=anthropic u openai "
            f"en .env; valor actual: {provider or 'vacío'!r}."
        )
    key_name = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider]
    key = (env.get(key_name) or "").strip()
    if not key:
        raise RuntimeError(
            f"Falta {key_name} en .env para CHAT_PROVIDER={provider}."
        )
    model = (env.get("CHAT_MODEL") or "").strip()
    if not model and provider == "openai":
        raise RuntimeError("OpenAI requiere CHAT_MODEL explícito en .env; evalúa ese modelo.")
    model = model or CHAT_MODEL
    return provider, model, key


def build_llm(provider: str, model: str, key: str) -> Any:
    """Chat model con las herramientas, una por turno (la tarjeta lo exige)."""
    chat = build_chat_model(
        provider, model, api_key=key, max_output_tokens=1500, timeout=90, max_retries=1
    )
    # Las propuestas admiten campos omitidos: strict=True los haría obligatorios en OpenAI.
    # La API valida cada propuesta antes de mostrar la tarjeta (0039).
    kwargs = {"strict": False} if provider == "openai" else {}
    return chat.bind_tools(TOOLS, parallel_tool_calls=False, **kwargs)


_llm: Any = None
_llm_meta: tuple[str, str] = ("", "")  # proveedor y modelo, para la traza del turno

BUDGET_EXHAUSTED = (
    "Llegamos al límite de mensajes automáticos para esta solicitud. Quedó pendiente de "
    "revisión por un asesor; no es un rechazo y tus datos están guardados."
)
TURN_UNAVAILABLE = (
    "No pude continuar en este momento; tus datos están guardados. Intenta de nuevo en unos "
    "minutos."
)
MODEL_CHANGED = (
    "La configuración del asistente cambió. Abre una conversación nueva para continuar; "
    "los datos ya guardados de tu solicitud se conservan."
)
MODEL_REFUSED = (
    "No pude procesar esa petición. Puedes reformularla o pedir ayuda a un asesor."
)

STAGE_LABELS = {
    "VEHICLE_ELIGIBILITY": "revisión del vehículo", "PROFILING": "datos personales y financieros",
    "SIMULATION": "opciones de crédito", "DOCUMENT_COLLECTION": "recepción de documentos",
    "DOCUMENT_VALIDATION": "revisión de documentos",
    "NEEDS_CORRECTION": "corrección de documentos o datos",
    "HUMAN_REVIEW": "pendiente de revisión por un asesor",
    "READY_FOR_FINANCIAL": "listo para revisión de la financiera",
    "REJECTED": "no es posible continuar con esta solicitud",
}
EXTRACTION_PENDING = (
    "Tus documentos recibidos están guardados, pero no pudimos completar su lectura. "
    "No hay una lectura ejecutándose en segundo plano. Puedes pedirme que lo reintente "
    "o solicitar la revisión de un asesor."
)


def _display_number(value: Any, *, percentage: bool = False) -> str:
    """Formato de cifras de la API; no calcula cuotas, ingresos ni condiciones nuevas."""
    try:
        number = Decimal(str(value))
        if number.is_finite():
            return f"{number * 100 if percentage else number:,.2f}"
    except InvalidOperation:
        pass
    return "no disponible"


def _selected_offer_text(snap: dict[str, Any]) -> str:
    offer = _selected_offer(snap)
    if offer is None:
        return "No tengo el detalle de la oferta seleccionada; no puedo confirmar sus importes."
    text = (
        "Estas son las condiciones registradas de la oferta que seleccionaste "
        "(importes en MXN):\n\n"
        f"- Efectivo solicitado: {_display_number(offer.get('cash_amount'))}.\n"
        f"- Plazo: {int(offer['term_months'])} meses.\n"
        f"- Costo de segunda llave incluido: {_display_number(offer.get('key_cost'))}.\n"
        f"- Capital financiado: {_display_number(offer.get('financed_principal'))}.\n"
        f"- Tasa nominal anual: "
        f"{_display_number(offer.get('annual_nominal_rate'), percentage=True)} %.\n"
        f"- Cuota mensual regular: {_display_number(offer.get('regular_payment'))}.\n"
        f"- Último pago: {_display_number(offer.get('last_payment'))}.\n"
        f"- Total de pagos del plan: {_display_number(offer.get('total_payment'))}."
    )
    if offer.get("expired"):
        text += (
            "\n\nLa vigencia de esa cotización venció; estos datos son históricos, "
            "no una nueva oferta."
        )
    return text


def _ready_reply(snap: dict[str, Any], question: str) -> str:
    # Selección de vistas de lectura, no de acciones ni reglas. No se copia texto libre del
    # usuario/modelo. Las preguntas no reconocidas conservan el cierre seguro y las opciones.
    words = "".join(c for c in unicodedata.normalize("NFD", question.lower())
                    if not unicodedata.combining(c))
    details = bool(re.search(r"\b(detall\w*|resum\w*|proceso|document\w*|validacion\w*)", words))
    financial = bool(re.search(
        r"\b(cuanto|montos?|prestamos?|creditos?|efectivo|cuotas?|pagos?|pagar\w*|plazos?|"
        r"meses|tasas?|interes\w*|llaves?|total|costo\w*|ofertas?|elegi\w*|seleccion\w*|"
        r"mensualidad\w*)\b", words,
    ))
    parts = []
    if details:
        parts.append(
            "Completaste la confirmación de los datos del auto y del perfil, la selección de "
            "una oferta y la entrega de documentos. Las validaciones documentales y la "
            "verificación final del expediente pasaron; no quedaron correcciones ni revisiones "
            "abiertas al completar el proceso."
        )
    if details or financial:
        parts.append(_selected_offer_text(snap))
    parts.append(
        "Tu expediente está completo y listo para revisión de la financiera. Esto todavía "
        "no significa que el crédito esté aprobado ni que se haya realizado un desembolso."
    )
    parts.append(
        "Esta solicitud está cerrada a cambios, pero puedes consultar la oferta seleccionada "
        "o el resumen del proceso. Para otra solicitud, abre una conversación nueva."
    )
    return "\n\n".join(parts)


def _income_correction(snap: dict[str, Any]) -> str | None:
    """No inducir cambios de declaración ni exigir igualdad de importes de períodos distintos.

    Solo presenta el mismatch ya decidido por la API; no recalcula ni oculta otras fallas.
    """
    action = snap.get("next_action") or {}
    if (action.get("type") != "CORRECT_DOCUMENTS"
            or {(r.get("rule_id"), r.get("reason_code")) for r in action.get("reasons", [])}
            != {("INCOME_MATCH", "INCOME_MISMATCH")}):
        return None
    income = (snap.get("profile") or {}).get("income") or {}
    period = {"MONTHLY": "al mes", "SEMIMONTHLY": "por quincena (dos veces al mes)",
              "BIWEEKLY_14D": "cada 14 días", "WEEKLY": "por semana"}.get(income.get("period"))
    declared = (
        f"Tu ingreso declarado es de {_display_number(income.get('amount'))} MXN netos {period}. "
        if period and income.get("currency") == "MXN" and income.get("basis") == "NET" else ""
    )
    return (
        declared + "El comprobante de ingresos no respalda la declaración al comparar "
        "importes con la misma periodicidad. Si lo declarado es correcto, adjunta el "
        "comprobante correspondiente, completo y legible, con «Upload PDF or Image». "
        "El recibo puede mostrar un importe distinto si cubre otro período: se considera "
        "su periodicidad, no se exige que muestre el importe mensual. Si el error está en "
        "lo declarado, indícame el monto neto y cada cuánto lo recibes para revisarlo "
        "antes de que lo confirmes."
    )


def customer_reply(response: AIMessage, snap: dict[str, Any], *, question: str = "") -> AIMessage:
    """Presentación del servidor; nunca modifica herramientas, datos ni reglas de negocio.

    Los resultados sensibles se redactan desde el snapshot, no desde una promesa del modelo.
    El resto conserva el diálogo libre con un filtro defensivo de identificadores conocidos.
    """
    if response.tool_calls:
        # Aún no ocurrió la acción. No publicar preámbulos que anuncien éxito o aprobación.
        # Conservar el contexto cifrado de modelos de razonamiento para la vuelta de herramienta.
        blocks = response.content if isinstance(response.content, list) else []
        content = [b for b in blocks if isinstance(b, dict) and b.get("type") == "reasoning"]
        return response.model_copy(update={"content": content or ""})
    stage = snap.get("workflow_state")
    if stage == "READY_FOR_FINANCIAL":
        text = _ready_reply(snap, question)
    elif stage == "HUMAN_REVIEW":
        text = (
            "Tu solicitud quedó pendiente de revisión por un asesor; no está aprobada ni resuelta. "
            "Esta demo no realiza llamadas ni envía mensajes de contacto automáticamente."
        )
    elif stage == "REJECTED":
        reasons = (snap.get("eligibility") or {}).get("rejection_reasons") or []
        explanations = {"VEHICLE_NOT_OWNED": "el auto no está a tu nombre",
                        "BLOCKING_DEBT": "el auto tiene un adeudo que impide usarlo como garantía"}
        reason = "; ".join(explanations[r] for r in reasons if r in explanations)
        text = "No podemos continuar con esta solicitud" + (f": {reason}." if reason else ".")
        text += " Está cerrada a cambios; para otra solicitud, abre una conversación nueva."
    elif snap.get("wait_reason") == "MODEL_UNAVAILABLE":
        text = EXTRACTION_PENDING
    elif stage == "NEEDS_CORRECTION" and (correction := _income_correction(snap)):
        text = correction
    else:
        text = response.text
        for code, label in STAGE_LABELS.items():
            text = re.sub(rf"\b{code}\b", label, text, flags=re.IGNORECASE)
        # Fallar cerrado ante detalles técnicos o promesas de contacto no implementado.
        internals = "|".join(re.escape(t.name) for t in TOOLS)
        internals += "|workflow_state|wait_reason|pending_action|" + "|".join(READABLE)
        contact = r"\b(?:contactar\w*|contactará\w*|llamar\w*|llamará\w*)\b"
        if (re.search(rf"\b(?:{internals})\b", text, re.IGNORECASE)
                or re.search(r"\b[A-Z]{2,}_[A-Z_]{2,}\b", text)
                or re.search(contact, text, re.IGNORECASE)
                or re.search(r'```json|\{\s*"', text)):
            text = (
                "Tu solicitud está en la etapa de " + STAGE_LABELS.get(stage, "revisión") + ". "
                "No se realizan llamadas ni mensajes de contacto automáticamente. "
                "Puedes preguntarme qué falta para continuar."
            )
    return response.model_copy(update={"content": text})


def get_llm() -> Any:
    global _llm, _llm_meta
    if _llm is None:
        provider, model, key = chat_settings()
        _llm, _llm_meta = build_llm(provider, model, key), (provider, model)
    return _llm


@recover_api_errors
@observe_node("agent.step", kind="agent")
async def agent(state: State, config: RunnableConfig) -> dict[str, Any]:
    """Una llamada al modelo por turno, siempre con turno y presupuesto concedidos por la API."""
    try:
        llm = get_llm()
    except RuntimeError as exc:  # sin proveedor o sin clave: se dice en el chat qué falta
        return {"messages": [AIMessage(str(exc))]}
    route = {"provider": _llm_meta[0], "model": _llm_meta[1]}
    if state.get("model_route") and state["model_route"] != route:
        # No reenviar contexto ni repetir herramientas con un modelo distinto silenciosamente.
        return {"messages": [AIMessage(MODEL_CHANGED)], "turn_failed": True}
    case_id = state["case_id"]
    status, body = await _api(config, "POST", f"/cases/{case_id}/agent-turns")
    if status == 409 and body.get("code") in ("TURN_BUDGET_EXCEEDED", "TOKEN_BUDGET_EXCEEDED"):
        return {"messages": [AIMessage(BUDGET_EXHAUSTED)]}
    if status not in (200, 201):
        return {"messages": [AIMessage(TURN_UNAVAILABLE)]}
    used, outcome = {}, "ERROR"
    try:
        provider, model = _llm_meta
        with get_tracer().span("agent.turn", kind="generation", metadata={
            "provider": provider, "model": model, "operation_id": body["turn_id"],
        }) as span:
            # No transmitir tokens sin validar. LangGraph emite después el mensaje del nodo,
            # ya revisado, y conserva SSE, tarjetas y resultados de herramientas (0044).
            response = await llm.ainvoke(
                [SystemMessage(PROMPT), *_for_model(state["messages"])],
                config={"tags": [TAG_NOSTREAM]},
            )
            used, outcome = getattr(response, "usage_metadata", None) or {}, "OK"
            if is_refusal(response) or is_incomplete(response) or response.invalid_tool_calls:
                # No ejecutar ni anunciar como completa una herramienta truncada/malformada.
                text = MODEL_REFUSED if is_refusal(response) else TURN_UNAVAILABLE
                response = response.model_copy(update={
                    "content": text, "tool_calls": [], "invalid_tool_calls": [],
                })
                outcome = "ERROR"
            span.update(metadata={"outcome": outcome}, usage=token_usage(used) if used else None)
    except (AnthropicAPIError, OpenAIAPIError, httpx.HTTPError, TimeoutError):
        # No se devuelve el mensaje del proveedor: puede contener detalles de la petición.
        response = AIMessage(TURN_UNAVAILABLE)
    finally:
        provider, model = _llm_meta
        report = {"input_tokens": int(used.get("input_tokens") or 0),
                  "output_tokens": int(used.get("output_tokens") or 0),
                  "provider": provider, "model": model, "outcome": outcome}  # fmt: skip
        try:
            await _api(
                config, "POST", f"/cases/{case_id}/agent-turns/{body['turn_id']}/usage", body=report
            )
        except (APIUnavailable, httpx.HTTPError):
            pass  # la API libera la reserva sin liquidar a los 10 minutos
    if isinstance(response, AIMessage) and len(response.tool_calls) > 1:
        response = response.model_copy(update={"tool_calls": response.tool_calls[:1]})
    if outcome == "OK":
        snap = {} if response.tool_calls else await _snapshot(config, case_id)
        question = next((m.text for m in reversed(state["messages"])
                         if isinstance(m, HumanMessage)), "")
        response = customer_reply(response, snap, question=question)
    return {"messages": [response], "model_route": route}


def _attachment(messages: list, filename: str) -> tuple[bytes, str] | None:
    """Busca el adjunto por nombre en los mensajes de la persona (del más reciente al primero)."""
    for m in reversed(messages):
        if not isinstance(m, HumanMessage) or not isinstance(m.content, list):
            continue
        for block in m.content:
            is_file = isinstance(block, dict) and block.get("type") in ("image", "file")
            if is_file and _block_name(block) == filename:
                raw = block.get("data") or block.get("base64") or ""
                return base64.b64decode(raw), filename
    return None


def without_attachment(messages: list, filename: str) -> HumanMessage | None:
    """Copia del mensaje con ese adjunto reemplazado por texto (mismo id, así lo sustituye).

    Una vez subido a la API, el archivo no tiene por qué seguir en el estado del hilo.
    """
    for m in reversed(messages):
        if not isinstance(m, HumanMessage) or not isinstance(m.content, list):
            continue
        blocks = m.content
        for i, block in enumerate(blocks):
            is_file = isinstance(block, dict) and block.get("type") in ("image", "file")
            if is_file and _block_name(block) == filename:
                note = {"type": "text", "text": f"[Adjunto subido: {filename}]"}
                return HumanMessage(content=[*blocks[:i], note, *blocks[i + 1 :]], id=m.id)
    return None


async def _upload(state: State, config: RunnableConfig, args: dict[str, Any]) -> dict[str, Any]:
    kind = args.get("tipo")
    if kind not in SLOTS:
        return {"error": f"Tipo inválido: {kind}"}
    found = _attachment(state["messages"], args.get("archivo", ""))
    if found is None:
        return {"error": "No encontré ese adjunto; pide que lo adjunte de nuevo."}
    content, name = found
    snap = await _snapshot(config, state["case_id"])
    current = next((d for d in snap.get("documents", []) if d["slot"] == SLOTS[kind]), None)
    form = {"declared_type": kind}
    if current:
        form["supersedes_id"] = current["document_id"]
    status, body = await _api(
        config,
        "POST",
        f"/cases/{state['case_id']}/documents",
        version=snap["case_version"],
        files={"file": (name, content, "application/octet-stream")},
        data=form,
    )
    if status not in (200, 201):
        return {"error": body.get("code"), "detalle": body.get("message")}
    doc = next((d for d in body.get("documents", []) if d["slot"] == SLOTS[kind]), None)
    extracted = doc is not None and doc["status"] == "EXTRACTED"
    progress = "EXTRACTED" if extracted else "RECEIVED"
    message = (
        "Documento recibido y leído. La lectura no significa validación ni aprobación del crédito."
        if extracted else "Documento recibido; su lectura sigue pendiente."
    )
    if extracted and body["workflow_state"] == "NEEDS_CORRECTION":
        progress = "NEEDS_CORRECTION"
        message += " El expediente requiere correcciones; consulta los motivos de la revisión."
    if body.get("wait_reason") == "MODEL_UNAVAILABLE":
        message = EXTRACTION_PENDING
    return {"resultado": message, "progreso": progress, "solicitud": _summary(body)}


@recover_api_errors
@observe_node("agent.tools", kind="tool")
async def tools_node(state: State, config: RunnableConfig) -> dict[str, Any]:
    """Herramientas sin aprobación: consultar, subir un adjunto, reintentar y pedir asesor."""
    results: list = []
    for call in state["messages"][-1].tool_calls:
        name = call["name"]
        if name == "consultar_solicitud":
            content: Any = _summary(await _snapshot(config, state["case_id"]))
        elif name == "subir_documento":
            content = await _upload(state, config, call["args"])
            stripped = without_attachment(state["messages"], call["args"].get("archivo", ""))
            if "error" not in content and stripped is not None:
                results.append(stripped)
        elif name == "reintentar":
            status, body = await _api(config, "POST", f"/cases/{state['case_id']}/runs")
            content = _summary(body) if status == 200 else {"error": body.get("code")}
        elif name == "pedir_asesor":
            snap = await _snapshot(config, state["case_id"])
            status, body = await _api(
                config, "POST", f"/cases/{state['case_id']}/review-requests",
                version=snap["case_version"],
            )  # fmt: skip
            content = _summary(body) if status == 200 else {"error": body.get("code")}
        else:
            content = {"error": "Herramienta no permitida."}
        text = json.dumps(content, ensure_ascii=False)
        results.append(ToolMessage(text, tool_call_id=call["id"], name=name))
    return {"messages": results}


def _profile_fields(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": a.get("full_name"),
        "address": {
            "street": a.get("street"),
            "external_number": a.get("external_number"),
            "internal_number": a.get("internal_number") or None,
            "neighborhood": a.get("neighborhood"),
            "municipality": a.get("municipality"),
            "state": a.get("state"),
            "postal_code": a.get("postal_code"),
            "country": "MX",
        },
        "employment": a.get("employment"),
        "employer_or_activity": a.get("employer_or_activity"),
        "income": {
            "amount": str(a.get("net_income_amount")).replace(",", ""),
            "currency": "MXN",
            "period": a.get("income_period"),
            "basis": "NET",
        },
    }


def _readable(fields: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in fields.items():
        if key == "declared_owner_name":
            continue
        if isinstance(value, bool):
            value = "Sí" if value else "No"
        elif key == "address":
            inner = f" int. {value['internal_number']}" if value.get("internal_number") else ""
            value = (
                f"{value['street']} {value['external_number']}{inner}, {value['neighborhood']}, "
                f"{value['municipality']}, {value['state']}, CP {value['postal_code']}"
            )
        elif key == "income":
            period = PERIODS.get(value["period"], value["period"])
            value = f"${value['amount']} MXN netos, {period}"
        elif key == "employment":
            value = EMPLOYMENT.get(value, value)
        out[READABLE.get(key, key)] = value
    return out


def _number(raw: Any) -> float | None:
    try:
        value = float(str(raw).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


@recover_api_errors
@observe_node("agent.prepare", kind="tool")
async def prepare(state: State, config: RunnableConfig) -> dict[str, Any]:
    """La API valida y persiste la propuesta; los datos solo se guardan tras aprobar."""
    call = state["messages"][-1].tool_calls[0]
    snap = await _snapshot(config, state["case_id"])
    if call["name"] == "preparar_eleccion":
        term = call["args"].get("plazo_meses")
        cash = _number(call["args"].get("efectivo"))
        offers = [o for o in snap.get("offers", []) if not o.get("expired")]
        offer = next(
            (o for o in offers
             if o["term_months"] == term and cash is not None
             and float(o["cash_amount"]) == cash),
            None,
        )  # fmt: skip
        if offer is None:
            error = "No hay una opción vigente con ese plazo y ese efectivo; elige una de la lista."
            return {"pending": {"error": error, "tool_call_id": call["id"]}}
        v = _offer_view(offer)
        card = {
            "Plazo": f"{v['plazo_meses']} meses",
            "Cuota mensual": f"${v['cuota_mensual']}",
            "Último pago": f"${v['ultimo_pago']}",
            "Efectivo para ti": f"${v['efectivo']}",
            "Costo de la llave": f"${v['costo_llave']}",
            "Total a pagar": f"${v['total']}",
            "Tasa anual": v["tasa_anual"],
        }
        pending = {"kind": "selection", "offer": offer, "card": card}
    else:
        group = "vehicle" if call["name"] == "proponer_datos_del_auto" else "profile"
        args = {k: v for k, v in call["args"].items() if v is not None}
        fields = args if group == "vehicle" else _profile_fields(args)
        if group == "vehicle":
            known = {**(snap.get("vehicle") or {}), **fields}
            required = ("owned_by_customer", "blocking_debt", "has_second_key",
                        "make", "model", "year", "vehicle_ref")
            missing = [k for k in required if known.get(k) is None or known.get(k) == ""]
            early_stop = (
                known.get("owned_by_customer") is False or known.get("blocking_debt") is True
            )
            if not fields or (missing and not early_stop):
                error = {"faltan": missing, "instruccion": (
                    "No hay tarjeta todavía. Recupera los datos ya respondidos del historial y "
                    "vuelve a proponer con ellos; conserva false. Si realmente falta un dato, "
                    "pregunta solo el primero que no haya sido respondido. No repitas preguntas."
                )}
                return {"pending": {"error": error, "tool_call_id": call["id"]},
                        "capture_errors": state.get("capture_errors", 0) + 1}
        status, body = await _api(
            config,
            "POST",
            f"/cases/{state['case_id']}/declaration-proposals",
            version=snap["case_version"],
            body={"group": group, "fields": fields},
        )
        if status != 200 or not body.get("pending_action"):
            error = body.get("message") or body.get("code") or f"HTTP {status}"
            if status == 422:
                error = {"datos_invalidos": error, "instruccion": "Pregunta de nuevo esos datos."}
            return {"pending": {"error": error, "tool_call_id": call["id"]}}
        action = body["pending_action"]
        snap = body
        pending = {"kind": "declarations", "action": action, "card": _readable(action["fields"])}
    pending |= {"case_version": snap["case_version"], "tool_call_id": call["id"]}
    return {"pending": pending}


@recover_api_errors
async def approval(state: State, config: RunnableConfig) -> dict[str, Any]:
    """Pausa hasta que la persona decida. Solo su «aprobar» llama a la API."""
    pending = state["pending"]
    call_id = pending["tool_call_id"]
    if "error" in pending:
        text = json.dumps({"error": pending["error"]}, ensure_ascii=False)
        messages = [ToolMessage(text, tool_call_id=call_id)]
        failed = state.get("capture_errors", 0) >= 2
        if failed:
            messages.append(AIMessage(
                "No pude preparar correctamente la tarjeta de datos del auto. No se guardó "
                "ningún cambio. Puedes pedirme que lo intente otra vez o solicitar un asesor."
            ))
        return {"messages": messages, "pending": None, "turn_failed": failed}
    is_selection = pending["kind"] == "selection"
    action_name = "elegir_esta_opcion" if is_selection else "confirmar_datos"
    description = (
        "Elige esta opción solo si es la que quieres."
        if is_selection
        else "Revisa tus datos. **No se guarda nada hasta que apruebes.**"
    )
    answer = interrupt(
        {
            "action_requests": [
                {"name": action_name, "args": pending["card"], "description": description}
            ],
            "review_configs": [
                {"action_name": action_name, "allowed_decisions": ["approve", "reject"]}
            ],
        }
    )
    decision = ((answer or {}).get("decisions") or [{}])[0]
    if decision.get("type") != "approve":
        comment = decision.get("message") or "sin comentario"
        result: dict[str, Any] = {"aprobado": False, "comentario": comment}
    else:
        if is_selection:
            offer = pending["offer"]
            body_in = {"offer_id": offer["offer_id"], "displayed_offer_hash": offer["display_hash"]}
            path = f"/cases/{state['case_id']}/selections"
        else:
            action = pending["action"]
            body_in = {
                "pending_action_id": action["pending_action_id"],
                "payload_hash": action["payload_hash"],
            }
            path = f"/cases/{state['case_id']}/confirmations"
        status, body = await _api(
            config, "POST", path, version=pending["case_version"], body=body_in
        )
        if status == 200:
            result = {"aprobado": True, "solicitud": _summary(body)}
        else:
            result = {"aprobado": True, "error": body.get("code"), "detalle": body.get("message")}
    text = json.dumps(result, ensure_ascii=False)
    return {"messages": [ToolMessage(text, tool_call_id=call_id)], "pending": None}


# --- Rutas -------------------------------------------------------------------------------


def after_start(state: State) -> str:
    if state.get("turn_failed"):
        return END
    return "reshow" if state.get("pending") else "agent"


def after_tool(state: State) -> str:
    return END if state.get("turn_failed") else "agent"


def after_prepare(state: State) -> str:
    return END if state.get("turn_failed") else "approval"


def after_agent(state: State) -> str:
    calls = getattr(state["messages"][-1], "tool_calls", None) or []
    if not calls:
        return END
    return "prepare" if calls[0]["name"] in APPROVAL_TOOLS else "tools"


builder = StateGraph(State)
builder.add_node("ensure_case", ensure_case)
builder.add_node("reshow", reshow)
builder.add_node("agent", agent)
builder.add_node("tools", tools_node)
builder.add_node("prepare", prepare)
builder.add_node("approval", approval)
builder.add_edge(START, "ensure_case")
builder.add_conditional_edges("ensure_case", after_start, ["reshow", "agent", END])
builder.add_edge("reshow", "approval")
builder.add_conditional_edges("agent", after_agent, ["prepare", "tools", END])
builder.add_conditional_edges("tools", after_tool, ["agent", END])
builder.add_conditional_edges("prepare", after_prepare, ["approval", END])
# Tras la decisión, el resultado vuelve al modelo como resultado de su herramienta y sigue.
builder.add_conditional_edges("approval", after_tool, ["agent", END])
graph = builder.compile()
