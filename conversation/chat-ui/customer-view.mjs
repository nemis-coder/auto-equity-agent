// Proyección de presentación: no cambia mensajes persistidos, herramientas ni decisiones.
export const stageLabels = {
  VEHICLE_ELIGIBILITY: "revisión del vehículo",
  PROFILING: "datos personales y financieros",
  SIMULATION: "opciones de crédito",
  DOCUMENT_COLLECTION: "recepción de documentos",
  DOCUMENT_VALIDATION: "revisión de documentos",
  NEEDS_CORRECTION: "corrección de documentos o datos",
  HUMAN_REVIEW: "pendiente de revisión por un asesor",
  READY_FOR_FINANCIAL: "listo para revisión de la financiera",
  REJECTED: "no es posible continuar con esta solicitud",
};

export function customerText(text, streaming = false) {
  const codes = Object.keys(stageLabels).join("|");
  // El paréntesis técnico redundante no aporta información al cliente.
  text = text.replace(new RegExp("\\(\\s*[`*]*(" + codes + ")[`*]*\\s*\\)", "gi"), "");
  text = text.replace(new RegExp(`\\b(${codes})\\b`, "gi"), (code) => stageLabels[code.toUpperCase()]);
  if (streaming) {
    // Un código puede llegar partido entre eventos SSE: no mostrar su prefijo aún.
    text = text.replace(/[A-Z_]+$/g, (tail) =>
      Object.keys(stageLabels).some((code) => code.startsWith(tail)) ? "" : tail,
    );
  }
  return text.replace(/ +([.,;:])/g, "$1");
}

const documentLabels = {
  IDENTITY: "Identificación",
  PAYSLIP: "Recibo de nómina",
  INCOME_STATEMENT: "Estado de cuenta",
  VEHICLE_OWNERSHIP: "Documento del vehículo",
};

const documentStatusLabels = {
  RECEIVED: "Recibido. Lectura pendiente.",
  EXTRACTED: "Leído. Consulta el resultado de la revisión en el chat.",
  NEEDS_CORRECTION: "Leído. El expediente requiere correcciones; consulta el chat.",
};

export function documentProgress(messages, loading) {
  // Solo el turno actual: no arrastrar actividad de otro mensaje o conversación.
  const start = messages.findLastIndex((m) => m.type === "human");
  if (start < 0) return [];
  const current = messages.slice(start + 1);
  const results = new Map(current.filter((m) => m.type === "tool").map((m) => [m.tool_call_id, m]));
  return current.flatMap((m) => (m.tool_calls || [])
    .filter((call) => call.name === "subir_documento" && call.id)
    .map((call) => {
      const result = results.get(call.id);
      let status = loading ? "Procesando…" : "Procesamiento interrumpido; consulta el estado antes de reintentar.";
      if (result) {
        try {
          const body = JSON.parse(result.content);
          status = body.error ? "No se pudo procesar. Revisa la indicación del asistente."
            : documentStatusLabels[body.progreso] || "Resultado pendiente de confirmar.";
        } catch { status = "Resultado pendiente de confirmar."; }
      }
      return { id: call.id, label: documentLabels[call.args?.tipo] || "Documento", status };
    }));
}
