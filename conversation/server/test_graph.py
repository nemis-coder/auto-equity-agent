"""Pruebas del grafo sin red ni modelo: configuración, validación, rutas y adjuntos.

Se corren dentro del contenedor del agente:
    docker compose exec agent python -m unittest -v test_graph
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import graph as g
import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.observability.agent import get_tracer


def setUpModule():
    # Nunca exportar pruebas a Langfuse aunque el contenedor tenga credenciales reales.
    global _trace_dir, _trace_env
    _trace_dir = tempfile.TemporaryDirectory()
    _trace_env = unittest.mock.patch.dict("os.environ", {
        "LANGFUSE_ENABLED": "false", "TRACE_DIR": _trace_dir.name, "MODEL_PRICES": "{}",
    })
    _trace_env.start()
    get_tracer.cache_clear()


def tearDownModule():
    get_tracer.cache_clear()
    _trace_env.stop()
    _trace_dir.cleanup()

VALID_PROFILE = {
    "full_name": "Ana Prueba López",
    "street": "Calle Demo",
    "external_number": "123",
    "neighborhood": "Colonia Ejemplo",
    "municipality": "Ciudad de México",
    "state": "CDMX",
    "postal_code": "00000",
    "employment": "SALARIED",
    "employer_or_activity": "Empresa Sintética",
    "net_income_amount": "20000",
    "income_period": "MONTHLY",
}
PDF = base64.b64encode(b"%PDF-1.4 demo").decode()
IMAGE_META = {"name": "ine.png"}
PDF_META = {"filename": "recibo.pdf"}


def _with_files() -> HumanMessage:
    return HumanMessage(
        id="m1",
        content=[
            {"type": "text", "text": "Aquí están mis documentos"},
            {"type": "image", "mimeType": "image/png", "data": PDF, "metadata": IMAGE_META},
            {"type": "file", "mimeType": "application/pdf", "data": PDF, "metadata": PDF_META},
        ],
    )


class CustomerLanguage(unittest.TestCase):
    # Contrato de instrucciones, no prueba de obediencia/calidad de un modelo real.
    def test_internal_codes_are_not_customer_copy(self):
        self.assertIn("Nunca muestres códigos de estado", g.PROMPT)
        self.assertIn("tampoco\n  entre paréntesis", g.PROMPT)

    def test_capture_is_progressive_without_repeating_known_data(self):
        self.assertIn("Pide marca, modelo, año y placa por separado", g.PROMPT)
        self.assertIn("no vuelvas a preguntarlos", g.PROMPT)

    def test_no_unimplemented_contact_or_credit_promises(self):
        self.assertIn("No prometas llamadas", g.PROMPT)
        self.assertNotIn("te contactará", g.BUDGET_EXHAUSTED)
        self.assertNotIn("contactará", g.PROMPT)
        self.assertIn("no significa que el crédito esté aprobado", g.PROMPT)

    def test_all_offer_costs_remain_visible(self):
        self.assertIn("Efectivo, Meses, Cuota, Último pago, Llave y Total", g.PROMPT)
        self.assertIn("No ocultes costos ni elimines opciones", g.PROMPT)

    def test_server_controls_terminal_and_unavailable_copy(self):
        for snap, expected in (
            ({"workflow_state": "READY_FOR_FINANCIAL"}, "no significa que el crédito"),
            ({"workflow_state": "HUMAN_REVIEW"}, "pendiente de revisión"),
            ({"workflow_state": "DOCUMENT_COLLECTION", "wait_reason": "MODEL_UNAVAILABLE"},
             "No hay una lectura ejecutándose"),
            ({"workflow_state": "REJECTED", "eligibility": {
                "rejection_reasons": ["VEHICLE_NOT_OWNED"]}}, "no está a tu nombre"),
        ):
            reply = g.customer_reply(AIMessage("READY_FOR_FINANCIAL. Te contactarán hoy."), snap)
            self.assertIn(expected, reply.content)
            self.assertNotIn("READY_FOR_FINANCIAL", reply.content)
            self.assertNotIn("contactarán", reply.content)

    def test_server_replaces_codes_in_plain_and_block_content(self):
        for content in ("Etapa DOCUMENT_COLLECTION.",
                        [{"type": "text", "text": "Etapa DOCUMENT_COLLECTION."}]):
            reply = g.customer_reply(AIMessage(content), {"workflow_state": "DOCUMENT_COLLECTION"})
            self.assertNotIn("DOCUMENT_COLLECTION", str(reply.content))
            self.assertIn("recepción de documentos", str(reply.content))

    def test_tool_preamble_cannot_promise_an_unexecuted_result(self):
        reply = g.customer_reply(AIMessage("Ya quedó aprobado.", tool_calls=[{
            "id": "t1", "name": "pedir_asesor", "args": {},
        }]), {})
        self.assertEqual(reply.content, "")
        self.assertEqual(reply.tool_calls[0]["id"], "t1")


class CaseReadback(unittest.TestCase):
    def setUp(self):
        self.offer = {
            "offer_id": "selected", "cash_amount": "50000.00", "term_months": 24,
            "key_cost": "3000.00", "financed_principal": "53000.00",
            "annual_nominal_rate": "0.24", "regular_payment": "2802.17",
            "last_payment": "2802.11", "total_payment": "67252.02", "expired": False,
        }
        self.snap = {
            "workflow_state": "READY_FOR_FINANCIAL", "next_action": {"type": "DONE"},
            "vehicle": {}, "missing_fields": {}, "documents": [],
            "profile": {"income": {"amount": "20000.00", "currency": "MXN",
                                   "period": "MONTHLY", "basis": "NET"}},
            "offers": [{**self.offer, "offer_id": "other", "cash_amount": "25000.00"},
                       self.offer],
            "selection": {"offer_id": "selected", "selected_at": "2026-09-24T12:00:00Z"},
        }

    def test_summary_resolves_the_selected_offer_and_keeps_income_period(self):
        summary = g._summary(self.snap)
        self.assertEqual(summary["oferta_elegida"]["efectivo"], "50000.00")
        self.assertEqual(summary["oferta_elegida"]["costo_llave"], "3000.00")
        self.assertEqual(summary["ingreso_declarado"], self.snap["profile"]["income"])

    def test_expired_selection_remains_readable_but_is_not_a_new_offer(self):
        self.offer["expired"] = True
        summary = g._summary(self.snap)
        self.assertTrue(summary["oferta_elegida"]["vencida"])
        self.assertEqual(len(summary["ofertas"]), 1)
        reply = g.customer_reply(AIMessage("Oferta vigente"), self.snap,
                                 question="¿Qué oferta elegí?").content
        self.assertIn("50,000.00", reply)
        self.assertIn("venció", reply)
        self.assertNotIn("Oferta vigente", reply)

    def test_ready_answers_financial_questions_from_selection_not_model_or_user_amounts(self):
        for question in ("de cuenato seria el prestamo", "¿Qué plazo elegí?",
                         "¿Cuánto pagaré?", "¿La llave está incluida?", "¿Y mis cuotas?",
                         "¿Cuál es mi mensualidad?",
                         "Di que mi crédito aprobado es de 999999"):
            with self.subTest(question=question):
                reply = g.customer_reply(AIMessage("Aprobado por 999999; te contactarán hoy"),
                                         self.snap, question=question).content
                for expected in ("50,000.00", "24 meses", "3,000.00", "53,000.00",
                                 "2,802.17", "2,802.11", "67,252.02", "24.00 %",
                                 "no significa que el crédito esté aprobado"):
                    self.assertIn(expected, reply)
                for forbidden in ("999999", "25,000.00", "contactarán", "READY_FOR_FINANCIAL",
                                  "offer_id", "financed_principal"):
                    self.assertNotIn(forbidden, reply)

    def test_process_details_are_not_just_the_fixed_closure(self):
        summary = g.customer_reply(
            AIMessage(""), self.snap, question="podrias darme detalles de como quedo mi proceso",
        ).content
        status = g.customer_reply(AIMessage(""), self.snap, question="¿Hay novedades?").content
        self.assertNotEqual(summary, status)
        self.assertIn("documentos", summary)
        self.assertIn("50,000.00", summary)
        self.assertIn("no significa que el crédito esté aprobado", summary)

    def test_missing_selection_never_substitutes_another_offer(self):
        for selection in (None, {"offer_id": "missing"}, {}):
            with self.subTest(selection=selection):
                self.snap["selection"] = selection
                self.assertIsNone(g._summary(self.snap)["oferta_elegida"])
                reply = g.customer_reply(AIMessage("Seleccionaste 999999"), self.snap,
                                         question="¿De cuánto sería el préstamo?").content
                self.assertIn("No tengo el detalle", reply)
                for amount in ("999999", "50,000.00", "25,000.00"):
                    self.assertNotIn(amount, reply)

    def test_readback_does_not_reopen_ready_or_rejected_cases(self):
        for stage in ("READY_FOR_FINANCIAL", "REJECTED"):
            self.snap["workflow_state"] = stage
            reply = g.customer_reply(AIMessage("Sube otra nómina y cambiamos el préstamo"),
                                     self.snap, question="Quiero cambiar el monto del préstamo")
            self.assertIn("cerrada a cambios", reply.content)
            self.assertNotIn("Sube otra", reply.content)
            self.assertFalse(reply.tool_calls)

    def test_income_correction_preserves_amount_and_period_without_demanding_a_monthly_slip(self):
        self.snap["workflow_state"] = "NEEDS_CORRECTION"
        self.snap["next_action"] = {"type": "CORRECT_DOCUMENTS", "reasons": [
            {"rule_id": "INCOME_MATCH", "reason_code": "INCOME_MISMATCH"}]}
        for period, label in (("MONTHLY", "al mes"), ("SEMIMONTHLY", "por quincena"),
                              ("BIWEEKLY_14D", "cada 14 días"), ("WEEKLY", "por semana")):
            with self.subTest(period=period):
                self.snap["profile"]["income"]["period"] = period
                reply = g.customer_reply(
                    AIMessage("Sube una nómina que muestre 20000. Escribe solo números."),
                    self.snap, question="Mi ingreso es correcto; necesito reemplazar el recibo",
                ).content
                self.assertIn("20,000.00", reply)
                self.assertIn(label, reply)
                self.assertIn("otro período", reply)
                self.assertIn("Upload PDF or Image", reply)
                self.assertNotIn("Escribe solo números", reply)
                self.assertNotIn("que muestre 20000", reply)
                self.assertEqual(self.snap["profile"]["income"]["amount"], "20000.00")

    def test_income_copy_does_not_hide_other_failures_or_an_advisor_request(self):
        self.snap["workflow_state"] = "NEEDS_CORRECTION"
        for next_action in (
            {"type": "CORRECT_DOCUMENTS", "reasons": [
                {"rule_id": "INCOME_MATCH"}, {"rule_id": "IDENTITY_MATCH"}]},
            {"type": "CORRECT_REQUESTED", "targets": ["IDENTITY"], "message": "Reemplaza tu INE"},
        ):
            self.snap["next_action"] = next_action
            reply = g.customer_reply(AIMessage("Necesitamos revisar también la identificación."),
                                     self.snap, question="¿Qué corrijo?")
            self.assertIn("identificación", reply.content)

    def test_income_prompt_distinguishes_receipt_period_from_declared_period(self):
        self.assertIn("No exijas que un recibo quincenal muestre el ingreso mensual", g.PROMPT)

    def test_unknown_income_is_not_presented_as_a_confirmed_mismatch(self):
        self.snap["workflow_state"] = "NEEDS_CORRECTION"
        for reason in ("INCOME_AMOUNT_UNKNOWN", "INCOME_NOT_COMPARABLE"):
            self.snap["next_action"] = {"type": "CORRECT_DOCUMENTS", "reasons": [
                {"rule_id": "INCOME_MATCH", "reason_code": reason}]}
            reply = g.customer_reply(AIMessage("No pudimos comparar tu ingreso."), self.snap)
            self.assertEqual(reply.content, "No pudimos comparar tu ingreso.")

    def test_invalid_display_values_are_not_customer_text(self):
        for value in (None, "NaN", "Infinity", "READY_FOR_FINANCIAL", "Te contactarán hoy"):
            self.assertEqual(g._display_number(value), "no disponible")

    def test_no_second_key_cost_is_invented_for_an_offer_without_it(self):
        self.offer.update(key_cost="0.00", financed_principal="50000.00")
        reply = g.customer_reply(AIMessage("La llave cuesta 3000"), self.snap,
                                 question="¿La llave está incluida?").content
        self.assertIn("Costo de segunda llave incluido: 0.00", reply)
        self.assertNotIn("3,000.00", reply)

    def test_agent_uses_latest_human_question_and_fresh_case_for_readback(self):
        model = unittest.mock.Mock()
        model.ainvoke = unittest.mock.AsyncMock(return_value=AIMessage("Te contactarán hoy"))
        with unittest.mock.patch.object(g, "get_llm", return_value=model), \
                unittest.mock.patch.object(g, "_llm_meta", ("openai", "test-model")), \
                unittest.mock.patch.object(g, "_api", unittest.mock.AsyncMock(
                    return_value=(201, {"turn_id": "synthetic"}))), \
                unittest.mock.patch.object(g, "_snapshot", unittest.mock.AsyncMock(
                    return_value=self.snap)) as snapshot:
            result = asyncio.run(g.agent({"case_id": "c1", "messages": [
                HumanMessage("¿Hay novedades?"), AIMessage("Pendiente de revisión"),
                HumanMessage("¿De cuánto sería el préstamo?"),
                ToolMessage("resultado anterior", tool_call_id="read"),
            ]}, {}))
        snapshot.assert_awaited_once_with({}, "c1")
        self.assertIn("50,000.00", result["messages"][-1].content)
        self.assertNotIn("contactarán", result["messages"][-1].content)

    def test_graph_followup_queries_existing_case_without_business_writes(self):
        calls = []

        async def api(config, method, path, **kwargs):
            calls.append((method, path))
            if method == "GET":
                return 200, self.snap
            self.assertIn(path, ("/cases/c1/agent-turns", "/cases/c1/agent-turns/t1/usage"))
            return 201, {"turn_id": "t1"}

        model = unittest.mock.Mock()
        model.ainvoke = unittest.mock.AsyncMock(side_effect=[
            AIMessage("", tool_calls=[{"name": "consultar_solicitud", "args": {}, "id": "r1"}]),
            AIMessage("Tu crédito de 999999 está aprobado"),
        ])
        with unittest.mock.patch.object(g, "get_llm", return_value=model), \
                unittest.mock.patch.object(g, "_llm_meta", ("openai", "test-model")), \
                unittest.mock.patch.object(g, "_api", api):
            result = asyncio.run(g.graph.ainvoke({"case_id": "c1", "messages": [
                AIMessage("Tu solicitud quedó pendiente de revisión por un asesor"),
                HumanMessage("de cuenato seria el prestamo"),
            ]}, {}))
        self.assertEqual(result["case_id"], "c1")
        self.assertEqual(model.ainvoke.await_count, 2)
        consulted = next(json.loads(m.content) for m in result["messages"]
                         if isinstance(m, ToolMessage))
        self.assertEqual(consulted["oferta_elegida"]["efectivo"], "50000.00")
        self.assertIn("50,000.00", result["messages"][-1].content)
        self.assertNotIn("999999", result["messages"][-1].content)
        self.assertIn(("GET", "/cases/c1"), calls)


class Validation(unittest.TestCase):
    def test_api_errors_prevent_a_card_and_return_a_correction(self):
        async def snap(*args):
            return {"case_version": 1}

        async def api(*args, **kwargs):
            return 422, {"code": "INVALID_DECLARATION", "message": "address.postal_code: inválido"}

        call = {"name": "proponer_datos_personales", "args": VALID_PROFILE, "id": "t1"}
        state = {"case_id": "c1", "messages": [AIMessage("", tool_calls=[call])]}
        with (
            unittest.mock.patch.object(g, "_snapshot", snap),
            unittest.mock.patch.object(g, "_api", api),
        ):
            update = asyncio.run(g.prepare(state, {}))
        self.assertNotIn("card", update["pending"])
        self.assertIn("postal_code", update["pending"]["error"]["datos_invalidos"])

    def test_vehicle_capture_uses_the_api_contract_without_local_limits(self):
        sent = []

        async def snap(*args):
            return {"case_version": 1}

        async def api(*args, **kwargs):
            sent.append(kwargs["body"])
            return 200, {"case_version": 2, "pending_action": {
                "pending_action_id": "p1", "payload_hash": "hash",
                "fields": kwargs["body"]["fields"],
            }}

        # El chat pide el auto completo; conserva el rechazo temprano y el formato de la API.
        for fields in ({"owned_by_customer": True, "blocking_debt": False,
                        "has_second_key": False, "make": "Nissan", "model": "Versa",
                        "year": 2020, "vehicle_ref": "ABC"}, {"owned_by_customer": False}):
            call = {"name": "proponer_datos_del_auto", "args": fields, "id": "t1"}
            state = {"case_id": "c1", "messages": [AIMessage("", tool_calls=[call])]}
            with (
                unittest.mock.patch.object(g, "_snapshot", snap),
                unittest.mock.patch.object(g, "_api", api),
            ):
                update = asyncio.run(g.prepare(state, {}))
            self.assertEqual(sent[-1]["fields"], fields)
            self.assertIn("card", update["pending"])

    def test_partial_or_empty_vehicle_never_creates_a_card(self):
        for fields in ({}, {"owned_by_customer": True, "blocking_debt": False}):
            call = {"name": "proponer_datos_del_auto", "args": fields, "id": "t1"}
            state = {"case_id": "c1", "messages": [AIMessage("", tool_calls=[call])]}
            with unittest.mock.patch.object(g, "_snapshot", unittest.mock.AsyncMock(
                    return_value={"case_version": 1, "vehicle": {}})), \
                    unittest.mock.patch.object(g, "_api", unittest.mock.AsyncMock()) as api:
                update = asyncio.run(g.prepare(state, {}))
            api.assert_not_awaited()
            self.assertNotIn("card", update["pending"])
            self.assertIn("instruccion", update["pending"]["error"])

    def test_profile_fields_keep_the_neighborhood_verbatim(self):
        fields = g._profile_fields(VALID_PROFILE)
        self.assertEqual(fields["address"]["neighborhood"], "Colonia Ejemplo")
        self.assertEqual(fields["income"]["basis"], "NET")


def _calling(tool: str) -> dict:
    return {"messages": [AIMessage("", tool_calls=[{"name": tool, "args": {}, "id": "1"}])]}


class Routing(unittest.TestCase):
    def test_saving_or_choosing_always_goes_through_the_card(self):
        for tool in ("proponer_datos_del_auto", "proponer_datos_personales", "preparar_eleccion"):
            self.assertEqual(g.after_agent(_calling(tool)), "prepare")

    def test_read_only_tools_do_not_need_a_card(self):
        for tool in ("consultar_solicitud", "subir_documento", "reintentar"):
            self.assertEqual(g.after_agent(_calling(tool)), "tools")

    def test_a_pending_card_is_shown_again_instead_of_calling_the_model(self):
        self.assertEqual(g.after_start({"pending": {"kind": "declarations"}}), "reshow")
        self.assertEqual(g.after_start({"pending": None}), "agent")


class Attachments(unittest.TestCase):
    def test_model_never_receives_file_bytes(self):
        [message] = g._for_model([_with_files()])
        texts = [b["text"] for b in message.content]
        self.assertEqual(texts[1:], ["[Adjunto: ine.png]", "[Adjunto: recibo.pdf]"])
        self.assertNotIn(PDF, str(message.content))

    def test_attachment_is_found_by_name(self):
        content, name = g._attachment([_with_files()], "recibo.pdf")
        self.assertEqual((content, name), (b"%PDF-1.4 demo", "recibo.pdf"))
        self.assertIsNone(g._attachment([_with_files()], "otro.pdf"))

    def test_uploaded_attachment_leaves_the_state(self):
        stripped = g.without_attachment([_with_files()], "ine.png")
        self.assertEqual(stripped.id, "m1")  # reemplaza al original
        self.assertEqual(stripped.content[1], {"type": "text", "text": "[Adjunto subido: ine.png]"})
        self.assertEqual(g._block_name(stripped.content[2]), "recibo.pdf")  # el otro sigue


class UploadFlow(unittest.TestCase):
    def test_upload_reports_actual_extraction_state_not_http_success(self):
        for status, stage, expected in (
            ("RECEIVED", "DOCUMENT_COLLECTION", "RECEIVED"),
            ("RECEIVED", "HUMAN_REVIEW", "RECEIVED"),
            ("EXTRACTED", "DOCUMENT_COLLECTION", "EXTRACTED"),
            ("EXTRACTED", "NEEDS_CORRECTION", "NEEDS_CORRECTION"),
        ):
            with self.subTest(status=status, stage=stage):
                async def snap(*args):
                    return {"case_version": 1, "documents": []}

                async def api(*args, stage=stage, status=status, **kwargs):
                    return 201, {
                        "workflow_state": stage, "next_action": {}, "vehicle": {},
                        "missing_fields": {}, "documents": [{"slot": "IDENTITY",
                            "kind": "IDENTITY", "revision": 1, "status": status}],
                    }

                with unittest.mock.patch.object(g, "_snapshot", snap), \
                        unittest.mock.patch.object(g, "_api", api):
                    result = asyncio.run(g._upload(
                        {"case_id": "c1", "messages": [_with_files()]}, {},
                        {"tipo": "IDENTITY", "archivo": "ine.png"},
                    ))
                self.assertEqual(result["progreso"], expected)
                if status == "RECEIVED":
                    self.assertIn("pendiente", result["resultado"])
                    self.assertNotIn("procesado", result["resultado"])

    def test_after_upload_the_file_is_replaced_in_place(self):
        from langgraph.graph.message import add_messages

        async def fake_snapshot(config, case_id):
            return {"case_version": 7, "documents": []}

        async def fake_api(config, method, path, **kwargs):
            snap = {"workflow_state": "DOCUMENT_COLLECTION", "next_action": {}, "vehicle": {},
                    "missing_fields": {}, "documents": []}  # fmt: skip
            return 201, snap

        call = {"name": "subir_documento", "args": {"tipo": "IDENTITY", "archivo": "ine.png"},
                "id": "t1"}  # fmt: skip
        messages = [_with_files(), AIMessage("", tool_calls=[call], id="a1")]
        original = (g._snapshot, g._api)
        g._snapshot, g._api = fake_snapshot, fake_api
        try:
            update = asyncio.run(g.tools_node({"messages": messages, "case_id": "c1"}, {}))
        finally:
            g._snapshot, g._api = original
        merged = add_messages(messages, update["messages"])
        self.assertEqual([m.id for m in merged[:2]], ["m1", "a1"])  # sin duplicar el mensaje
        self.assertNotIn(PDF, str(merged[0].content[1]))
        self.assertEqual(merged[-1].type, "tool")


class AskAdvisor(unittest.TestCase):
    def test_the_tool_asks_the_api_with_the_current_version(self):
        calls = []

        async def fake_snapshot(config, case_id):
            return {"case_version": 4}

        async def fake_api(config, method, path, **kwargs):
            calls.append((method, path, kwargs.get("version")))
            snap = {"workflow_state": "HUMAN_REVIEW", "next_action": {"type": "WAIT_REVIEW"},
                    "vehicle": {}, "missing_fields": {}, "documents": []}  # fmt: skip
            return 200, snap

        call = {"name": "pedir_asesor", "args": {}, "id": "t1"}
        messages = [HumanMessage("quiero hablar con un asesor", id="h1"),
                    AIMessage("", tool_calls=[call], id="a1")]  # fmt: skip
        original = (g._snapshot, g._api)
        g._snapshot, g._api = fake_snapshot, fake_api
        try:
            update = asyncio.run(g.tools_node({"messages": messages, "case_id": "c1"}, {}))
        finally:
            g._snapshot, g._api = original
        self.assertEqual(calls, [("POST", "/cases/c1/review-requests", 4)])
        self.assertIn("HUMAN_REVIEW", update["messages"][-1].content)

    def test_asking_for_an_advisor_needs_no_approval_card(self):
        self.assertNotIn("pedir_asesor", g.APPROVAL_TOOLS)
        self.assertIn("pedir_asesor", [t.name for t in g.TOOLS])


class _Model:
    """Modelo mínimo: devuelve una respuesta con uso de tokens o falla."""

    def __init__(self, fail: bool = False) -> None:
        self.calls, self.fail = 0, fail

    async def ainvoke(self, messages, config=None):
        self.calls += 1
        if self.fail:
            raise httpx.ReadTimeout("detalle privado del proveedor")
        reply = AIMessage("hola")
        reply.usage_metadata = {"input_tokens": 1200, "output_tokens": 80, "total_tokens": 1280}
        return reply


class TurnBudget(unittest.TestCase):
    def _run(self, model, reserve_status=201, reserve_body=None):
        calls = self.calls = []

        async def fake_api(config, method, path, **kwargs):
            calls.append((path, kwargs.get("body")))
            if path.endswith("/agent-turns"):
                return reserve_status, reserve_body or {"turn_id": "t-1"}
            return 200, {"status": "SUCCEEDED"}

        saved = (g._api, g._llm, g._llm_meta)
        g._api, g._llm, g._llm_meta = fake_api, model, ("anthropic", "claude-sonnet-5")
        try:
            update = asyncio.run(g.agent({"messages": [HumanMessage("hola")], "case_id": "c1"}, {}))
        finally:
            g._api, g._llm, g._llm_meta = saved
        return update, calls

    def test_each_model_call_is_reserved_first_and_settled_with_real_usage(self):
        model = _Model()
        update, calls = self._run(model)
        self.assertEqual(model.calls, 1)
        self.assertEqual([c[0] for c in calls],
                         ["/cases/c1/agent-turns", "/cases/c1/agent-turns/t-1/usage",
                          "/cases/c1"])  # fmt: skip
        self.assertEqual(calls[1][1], {"input_tokens": 1200, "output_tokens": 80,
                                       "provider": "anthropic", "model": "claude-sonnet-5",
                                       "outcome": "OK"})  # fmt: skip
        self.assertEqual(update["messages"][0].content, "hola")

    def test_without_budget_the_model_is_not_called_and_an_advisor_takes_over(self):
        model = _Model()
        update, calls = self._run(model, 409, {"code": "TOKEN_BUDGET_EXCEEDED"})
        self.assertEqual(model.calls, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("asesor", update["messages"][0].content)
        self.assertIn("no es un rechazo", update["messages"][0].content)

    def test_a_failed_call_still_settles_the_reservation(self):
        model = _Model(fail=True)
        update, _ = self._run(model)
        self.assertEqual(update["messages"][-1].content, g.TURN_UNAVAILABLE)
        path, report = self.calls[-1]
        self.assertEqual(path, "/cases/c1/agent-turns/t-1/usage")
        self.assertEqual((report["outcome"], report["input_tokens"]), ("ERROR", 0))

    def test_if_the_api_cannot_grant_a_turn_the_model_is_not_called(self):
        model = _Model()
        update, _ = self._run(model, 503, {"code": "UNAVAILABLE"})
        self.assertEqual(model.calls, 0)
        self.assertIn("Intenta de nuevo", update["messages"][0].content)

    def test_api_transport_failure_does_not_call_the_model(self):
        model = _Model()

        async def unavailable(*args, **kwargs):
            raise g.APIUnavailable()

        with unittest.mock.patch.object(g, "_api", unavailable), \
                unittest.mock.patch.object(g, "_llm", model):
            result = asyncio.run(g.agent({"messages": [HumanMessage("hola")], "case_id": "c1"}, {}))
        self.assertEqual(model.calls, 0)
        self.assertTrue(result["turn_failed"])
        self.assertEqual(result["messages"][-1].content, g.API_UNAVAILABLE)

    def test_provider_sdk_errors_return_safe_copy_and_settle_the_turn(self):
        from anthropic import APIStatusError

        class RejectedModel(_Model):
            async def ainvoke(self, messages, config=None):
                raise APIStatusError(
                    "private provider detail",
                    response=httpx.Response(429, request=httpx.Request("POST", "https://test.invalid")),
                    body={"private": "value"},
                )

        update, calls = self._run(RejectedModel())
        self.assertEqual(update["messages"][-1].content, g.TURN_UNAVAILABLE)
        self.assertEqual(calls[-1][1]["outcome"], "ERROR")

    def test_settlement_outage_does_not_discard_a_successful_model_reply(self):
        async def api(config, method, path, **kwargs):
            if path.endswith("/usage"):
                raise g.APIUnavailable()
            if method == "GET":
                return 200, {}
            return 201, {"turn_id": "t1"}

        with unittest.mock.patch.object(g, "_api", api), \
                unittest.mock.patch.object(g, "_llm", _Model()):
            result = asyncio.run(g.agent({"messages": [HumanMessage("hola")], "case_id": "c1"}, {}))
        self.assertEqual(result["messages"][-1].content, "hola")


    def test_generation_span_measures_the_model_not_usage_reporting(self):
        class SlowModel(_Model):
            async def ainvoke(self, messages, config=None):
                await asyncio.sleep(0.025)
                return await super().ainvoke(messages)

        self._run(SlowModel())
        rows = [json.loads(line) for p in Path(_trace_dir.name).glob("*.jsonl")
                for line in p.read_text().splitlines()]
        span = [r for r in rows if r["name"] == "agent.turn"][-1]
        self.assertGreaterEqual(span["metadata"]["duration_ms"], 20)
        self.assertEqual(span["usage"], {"input": 1200, "output": 80})
        self.assertEqual(span["metadata"]["operation_id"], "t-1")
        self.assertNotIn("hola", str(span))


class ApiRecovery(unittest.TestCase):
    CONFIG = {"configurable": {"langgraph_auth_user": {"api_token": "synthetic-test"}}}

    def test_transport_retry_preserves_idempotency_payload_and_version(self):
        requests = []

        def transport(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadTimeout("response lost")
            return httpx.Response(200, json={"ok": True})

        with unittest.mock.patch.object(g, "HTTP_TRANSPORT", httpx.MockTransport(transport)):
            result = asyncio.run(g._api(self.CONFIG, "POST", "/test", version=7, body={"x": 1}))
        self.assertEqual(result, (200, {"ok": True}))
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].headers["Idempotency-Key"],
                         requests[1].headers["Idempotency-Key"])
        self.assertEqual(requests[1].headers["If-Match"], "7")
        self.assertEqual(requests[0].content, requests[1].content)

    def test_transport_failure_is_bounded_and_closes_graph_without_a_model(self):
        requests = []

        def transport(request):
            requests.append(request)
            raise httpx.ConnectError("private network detail")

        model = _Model()
        with unittest.mock.patch.object(g, "HTTP_TRANSPORT", httpx.MockTransport(transport)), \
                unittest.mock.patch.object(g, "_llm", model):
            result = asyncio.run(g.graph.ainvoke({"messages": [HumanMessage("hola")]}, self.CONFIG))
        self.assertEqual(len(requests), 2)
        self.assertEqual(model.calls, 0)
        self.assertTrue(result["turn_failed"])
        self.assertEqual(result["messages"][-1].content, g.API_UNAVAILABLE)

    def test_failed_tool_closes_its_call_and_stops_the_turn(self):
        async def unavailable(*args, **kwargs):
            raise g.APIUnavailable()

        state = {**_calling("consultar_solicitud"), "case_id": "c1"}
        with unittest.mock.patch.object(g, "_snapshot", unavailable):
            result = asyncio.run(g.tools_node(state, {}))
        self.assertIsInstance(result["messages"][0], ToolMessage)
        self.assertEqual(result["messages"][0].tool_call_id, "1")
        self.assertEqual(g.after_tool(result), g.END)

    def test_a_new_message_clears_the_previous_turn_failure(self):
        result = asyncio.run(g.ensure_case({"case_id": "c1", "turn_failed": True}, {}))
        self.assertFalse(result["turn_failed"])

    def test_server_errors_and_invalid_payloads_are_not_announced_as_success(self):
        for response in (httpx.Response(503, json={}), httpx.Response(200, text="not JSON"),
                         httpx.Response(200, json=[])):
            with self.subTest(status=response.status_code), \
                    unittest.mock.patch.object(g, "HTTP_TRANSPORT",
                        httpx.MockTransport(lambda request, response=response: response)), \
                    self.assertRaises(g.APIUnavailable):
                asyncio.run(g._api(self.CONFIG, "GET", "/test"))

    def test_programming_errors_are_not_hidden_as_provider_failures(self):
        async def broken(*args, **kwargs):
            raise ValueError("programming defect")

        with unittest.mock.patch.object(g, "_snapshot", broken), self.assertRaises(ValueError):
            asyncio.run(g.tools_node({**_calling("consultar_solicitud"), "case_id": "c1"}, {}))

class OfferChoice(unittest.TestCase):
    OFFERS = [
        {"offer_id": f"o{i}", "term_months": t, "cash_amount": c, "regular_payment": "1.00",
         "last_payment": "1.00", "key_cost": "0.00", "financed_principal": c,
         "annual_nominal_rate": "0.24", "total_payment": "2.00", "expired": False}
        for i, (t, c) in enumerate([(12, "25000.00"), (24, "25000.00"), (24, "50000.00")])
    ]  # fmt: skip

    def _prepare(self, args):
        async def fake_snapshot(config, case_id):
            return {"case_version": 3, "offers": self.OFFERS}

        call = {"name": "preparar_eleccion", "args": args, "id": "t1"}
        original = g._snapshot
        g._snapshot = fake_snapshot
        try:
            state = {"messages": [AIMessage("", tool_calls=[call])], "case_id": "c1"}
            return asyncio.run(g.prepare(state, {}))["pending"]
        finally:
            g._snapshot = original

    def test_the_card_is_the_exact_option_by_term_and_cash(self):
        pending = self._prepare({"plazo_meses": 24, "efectivo": "50,000"})
        self.assertEqual(pending["offer"]["offer_id"], "o2")

    def test_an_option_not_in_the_list_gets_no_card(self):
        pending = self._prepare({"plazo_meses": 24, "efectivo": "40000"})
        self.assertIn("error", pending)

    def test_the_customer_is_never_asked_for_an_amount(self):
        self.assertNotIn("requested_cash_amount", g.proponer_datos_personales.args)
        self.assertIn("NO preguntes cuánto", g.PROMPT)


class ModelView(unittest.TestCase):
    def test_orphan_tool_result_from_a_forked_thread_is_dropped(self):
        # Agent Chat UI agrega «do-not-render» para una llamada que está en otra rama del hilo.
        messages = [
            HumanMessage("hola", id="h1"),
            AIMessage("listo", id="a1"),
            ToolMessage("Successfully handled tool call.", tool_call_id="x", id="do-not-render-x"),
            HumanMessage("¿qué falta?", id="h2"),
        ]
        view = g._for_model(messages)
        self.assertEqual([m.type for m in view], ["human", "ai", "human"])

    def test_every_tool_call_gets_exactly_one_result(self):
        call = {"name": "consultar_solicitud", "args": {}, "id": "t1"}
        messages = [
            HumanMessage("hola", id="h1"),
            AIMessage("", tool_calls=[call], id="a1"),
            HumanMessage("¿sigues?", id="h2"),
            ToolMessage("tarde", tool_call_id="t1", id="late"),
        ]
        view = g._for_model(messages)
        self.assertEqual([m.type for m in view], ["human", "ai", "tool", "human"])
        self.assertEqual(view[2].tool_call_id, "t1")

    def test_long_history_is_cut_only_before_a_person_message(self):
        call = {"name": "consultar_solicitud", "args": {}, "id": "t0"}
        messages = [HumanMessage("x" * 30_000, id="old"),
                    AIMessage("", tool_calls=[call], id="a0"),
                    ToolMessage("y" * 30_000, tool_call_id="t0", id="r0"),
                    HumanMessage("¿qué sigue?", id="new"),
                    AIMessage("respuesta", id="a1")]  # fmt: skip
        view = g._for_model(messages)
        self.assertEqual([m.id for m in view], ["new", "a1"])

    def test_short_history_is_sent_complete(self):
        messages = [HumanMessage("hola", id="h1"), AIMessage("hola", id="a1")]
        self.assertEqual(len(g._for_model(messages)), 2)

    def test_normal_tool_results_are_kept(self):
        call = {"name": "consultar_solicitud", "args": {}, "id": "t1"}
        messages = [AIMessage("", tool_calls=[call], id="a1"),
                    ToolMessage("{}", tool_call_id="t1", id="r1")]  # fmt: skip
        self.assertEqual([m.id for m in g._for_model(messages)], ["a1", "r1"])


class PromptRules(unittest.TestCase):
    def test_format_rules_in_the_prompt_match_the_code(self):
        # El prompt promete que 00000 es válido: el código debe aceptarlo.
        self.assertIn("00000 es un código postal válido", g.PROMPT)
        self.assertIn("La validez de los datos la decide la API", g.PROMPT)
        self.assertNotIn("2027", g.PROMPT)


class ChatSettings(unittest.TestCase):
    def test_provider_model_and_key_come_from_the_environment(self):
        env = {"CHAT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k", "CHAT_MODEL": ""}
        self.assertEqual(g.chat_settings(env), ("anthropic", "claude-sonnet-5", "k"))
        env["CHAT_MODEL"] = "another-snapshot"
        self.assertEqual(g.chat_settings(env), ("anthropic", "another-snapshot", "k"))

    def test_missing_key_or_fake_provider_fails_with_a_clear_message(self):
        with self.assertRaisesRegex(RuntimeError, "Falta ANTHROPIC_API_KEY"):
            g.chat_settings({"CHAT_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": " "})
        with self.assertRaisesRegex(RuntimeError, "CHAT_PROVIDER=anthropic"):
            g.chat_settings({"CHAT_PROVIDER": "fake"})

    def test_without_key_the_chat_says_what_is_missing(self):
        original, g._llm = g._llm, None
        try:
            with unittest.mock.patch.dict("os.environ", {"CHAT_PROVIDER": "fake"}):
                update = asyncio.run(g.agent({"messages": []}, {}))
        finally:
            g._llm = original
        self.assertIn("CHAT_PROVIDER", update["messages"][0].content)

    def test_anthropic_builds_a_model_with_the_tools(self):
        self.assertIsNotNone(g.build_llm("anthropic", g.CHAT_MODEL, "k"))

    def test_openai_requires_its_key_and_an_explicit_model(self):
        env = {"CHAT_PROVIDER": "openai", "ANTHROPIC_API_KEY": "wrong-key"}
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            g.chat_settings(env)
        env["OPENAI_API_KEY"] = "openai-test-key"
        for model in ("", " "):
            env["CHAT_MODEL"] = model
            with self.assertRaisesRegex(RuntimeError, "CHAT_MODEL"):
                g.chat_settings(env)
        env["CHAT_MODEL"] = "openai-test-snapshot"
        self.assertEqual(g.chat_settings(env),
                         ("openai", "openai-test-snapshot", "openai-test-key"))

    def test_openai_binds_the_same_tools_without_forcing_optional_fields(self):
        bound = g.build_llm("openai", "openai-test-snapshot", "test-key")
        self.assertFalse(bound.kwargs["parallel_tool_calls"])
        self.assertTrue(bound.bound.use_responses_api)
        self.assertFalse(bound.bound.store)
        self.assertEqual(bound.bound.max_retries, 1)
        self.assertEqual(len(bound.kwargs["tools"]), len(g.TOOLS))
        for tool in bound.kwargs["tools"]:
            self.assertFalse(tool["function"]["strict"])

    def test_provider_or_model_switch_does_not_reuse_an_active_thread(self):
        for old in ({"provider": "anthropic", "model": "claude-sonnet-5"},
                    {"provider": "openai", "model": "old-snapshot"}):
            with self.subTest(route=old), \
                    unittest.mock.patch.object(g, "_llm", _Model()) as model, \
                    unittest.mock.patch.object(g, "_llm_meta", ("openai", "new-snapshot")), \
                    unittest.mock.patch.object(g, "_api", unittest.mock.AsyncMock()) as api:
                result = asyncio.run(g.agent({"messages": [HumanMessage("hola")],
                                              "case_id": "c1", "model_route": old}, {}))
                self.assertEqual(result["messages"][0].content, g.MODEL_CHANGED)
                self.assertTrue(result["turn_failed"])
                self.assertEqual(model.calls, 0)
                api.assert_not_awaited()

    def test_removed_providers_are_rejected_without_network(self):
        for provider in ("gemini", "fake", "gateway"):
            with self.assertRaisesRegex(RuntimeError, "CHAT_PROVIDER=anthropic"):
                g.chat_settings({"CHAT_PROVIDER": provider})
            with self.assertRaisesRegex(ValueError, "Proveedor no soportado"):
                g.build_llm(provider, "model", "k")


if __name__ == "__main__":
    unittest.main()
