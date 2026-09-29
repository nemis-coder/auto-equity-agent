import assert from "node:assert/strict";
import test from "node:test";
import { customerText, documentProgress, stageLabels } from "./customer-view.mjs";

test("traduce todos los estados sin modificar el significado de negocio", () => {
  for (const [code, label] of Object.entries(stageLabels)) {
    assert.equal(customerText(code), label);
    assert.equal(customerText(code.toLowerCase()), label);
  }
});
test("omite el código redundante entre paréntesis", () => {
  assert.equal(customerText("Listo para revisión (READY_FOR_FINANCIAL). No es aprobación."),
    "Listo para revisión. No es aprobación.");
  assert.equal(customerText("Revisión (`HUMAN_REVIEW`)."), "Revisión.");
});
test("retiene prefijos técnicos en cada corte posible del streaming", () => {
  for (const code of Object.keys(stageLabels)) {
    for (let n = 1; n < code.length; n++) {
      assert.equal(customerText(`Tu solicitud: ${code.slice(0, n)}`, true), "Tu solicitud: ");
    }
  }
});
test("no cambia importes, datos ni advertencias", () => {
  const text = "ABC-123-XYZ, MXN $2,643.55. No es crédito aprobado ni desembolso.";
  assert.equal(customerText(text, true), text);
});
const call = (id = "c1", tipo = "IDENTITY") => ({ type: "ai", tool_calls: [{ id, name: "subir_documento", args: { tipo } }] });
const human = { type: "human", content: "Mis documentos" };
const result = (body, id = "c1") => ({ type: "tool", tool_call_id: id, content: JSON.stringify(body) });
test("informa progreso antes del resultado, sin mostrar nombre de herramienta", () => {
  assert.deepEqual(documentProgress([human, call()], true), [{ id: "c1", label: "Identificación", status: "Procesando…" }]);
});
test("procesado no equivale a validado ni aprobado", () => {
  const [item] = documentProgress([human, call(), result({ progreso: "EXTRACTED" })], false);
  assert.equal(item.status, "Leído. Consulta el resultado de la revisión en el chat.");
});
test("un error nunca se presenta como éxito", () => {
  const [item] = documentProgress([human, call(), result({ error: "PROVIDER_FAILED" })], false);
  assert.match(item.status, /No se pudo procesar/);
  assert.ok(!item.status.includes("PROVIDER_FAILED"));
});
test("un stream detenido no deja el documento eternamente procesando", () => {
  assert.match(documentProgress([human, call()], false)[0].status, /interrumpido/);
});
test("resultados malformados no se anuncian como éxito", () => {
  assert.match(documentProgress([human, call(), { type: "tool", tool_call_id: "c1", content: "no JSON" }], false)[0].status, /pendiente/);
});
test("correlaciona cada resultado por llamada y acepta reintentos", () => {
  const rows = documentProgress([human, call(), result({ error: "X" }), call("c2"), result({ progreso: "EXTRACTED" }, "c2"), call("c3", "PAYSLIP")], true);
  assert.equal(rows.length, 3);
  assert.match(rows[0].status, /No se pudo/);
  assert.match(rows[1].status, /^Leído/);
  assert.equal(rows[2].status, "Procesando…");
});
test("no arrastra documentos de un turno anterior", () => {
  assert.deepEqual(documentProgress([human, call(), human], true), []);
  assert.deepEqual(documentProgress([], false), []);
});
test("no presenta otras herramientas como actividad documental", () => {
  assert.deepEqual(documentProgress([human, { type: "ai", tool_calls: [{ name: "preparar_eleccion", id: "x" }] }], true), []);
});

test("recibido con extractor caído nunca se presenta como leído", () => {
  const [item] = documentProgress([human, call(), result({ progreso: "RECEIVED", resultado: "recibido" })], false);
  assert.equal(item.status, "Recibido. Lectura pendiente.");
});
test("la corrección pertenece al expediente y no implica aprobación", () => {
  const [item] = documentProgress([human, call(), result({ progreso: "NEEDS_CORRECTION" })], false);
  assert.match(item.status, /expediente requiere correcciones/);
});
test("un resultado histórico sin estado explícito no prueba procesamiento", () => {
  const [item] = documentProgress([human, call(), result({ resultado: "Documento recibido y procesado." })], false);
  assert.equal(item.status, "Resultado pendiente de confirmar.");
});
