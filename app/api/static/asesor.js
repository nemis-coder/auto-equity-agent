// Pantalla del asesor (decisión 0036). Solo llama a la API con el token del asesor; no decide
// negocio: estados, reglas y resoluciones válidas los calcula el backend.
"use strict";

const T = JSON.parse(document.getElementById("texts").textContent);
const $ = (id) => document.getElementById(id);
const state = { token: "", caseId: null, snap: null, events: [], providerStatus: {}, tab: 0 };

// ---------- utilidades ----------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "className") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function table(headers, rows) {
  return h("div", { className: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}

const money = (v) => (v == null ? "—" : `$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} MXN`);
const rate = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(2)} % anual`);
const when = (iso) => (iso || "").slice(0, 16).replace("T", " ");
const label = (dict, code) => dict[code] || code;

function describe(name, value) {
  if (["owned_by_customer", "blocking_debt", "has_second_key"].includes(name)) {
    return value === true ? "Sí" : value === false ? "No" : "No lo sé";
  }
  if (value == null) return "No lo sé";
  if (name === "address" && typeof value === "object") {
    const interior = value.internal_number ? ` int. ${value.internal_number}` : "";
    return `${value.street} ${value.external_number}${interior}, ${value.neighborhood}, ` +
      `${value.municipality}, ${value.state}, CP ${value.postal_code}`;
  }
  if (name === "income" && typeof value === "object") {
    return `${money(value.amount)} ${label(T.PERIODS, value.period).toLowerCase()}, neto`;
  }
  if (name === "employment") return label(T.EMPLOYMENT, value);
  return String(value);
}

function correctionText(v) {
  const ref = (v.refs && v.refs[0]) || {};
  const template = T.CORRECTIONS[v.reason_code] || "Revisa {slot}.";
  return template
    .replace("{slot}", (T.SLOTS[ref.slot] || "el documento").toLowerCase())
    .replace("{field}", T.DOCUMENT_FIELDS[ref.field] || "un dato");
}

function flash(kind, text) {
  $("flash").replaceChildren(h("div", { className: `banner ${kind}` }, text));
}

// ---------- API ----------

// `crypto.randomUUID` solo existe en contextos seguros (https o localhost); `getRandomValues`
// también funciona si la pantalla se abre por otro nombre de host de la red local.
function commandKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `advisor-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

async function api(method, path, { body, version } = {}) {
  const headers = { Authorization: `Bearer ${state.token}` };
  if (method !== "GET") headers["Idempotency-Key"] = commandKey();
  if (version != null) headers["If-Match"] = String(version);
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* sin cuerpo JSON */ }
  return { ok: res.ok, status: res.status, body: data, code: data && data.code };
}

function errorText(result) {
  return T.ERRORS[result.code] || (result.body && result.body.message) || "No se pudo completar.";
}

// ---------- sesión ----------

async function enter(token) {
  state.token = token.trim();
  const me = await api("GET", "/me");
  if (!me.ok || me.body.role !== "ADVISOR") {
    state.token = "";
    flash("err", me.ok ? "Ese token no es de un asesor." : "Token inválido.");
    return;
  }
  try { sessionStorage.setItem("advisorToken", state.token); } catch { /* sin almacenamiento */ }
  $("login").hidden = true;
  $("app").hidden = false;
  $("logout").hidden = false;
  $("who").textContent = me.body.alias;
  $("flash").replaceChildren();
  await refresh();
}

function logout() {
  try { sessionStorage.removeItem("advisorToken"); } catch { /* sin almacenamiento */ }
  location.reload();
}

// ---------- bandeja y lista de casos ----------

async function refresh() {
  const [inbox, cases] = await Promise.all([api("GET", "/reviews"), api("GET", "/cases")]);
  const open = inbox.ok ? inbox.body.items : [];
  $("inbox-count").textContent = `(${open.length})`;
  $("inbox").replaceChildren(open.length
    ? table(["Caso", "Motivo", "Etapa previa", "Abierta"], open.map((i) => [
      h("button", { className: "link", type: "button", onclick: () => selectCase(i.case_id) }, i.case_id.slice(0, 8)),
      label(T.REVIEW_REASONS, i.reason_code),
      label(T.STATES, i.resume_state),
      when(i.opened_at),
    ]))
    : h("p", { className: "muted" }, "No hay revisiones abiertas."));

  const items = cases.ok ? cases.body.items : [];
  const inReview = new Set(open.map((i) => i.case_id));
  const select = $("case-select");
  select.replaceChildren(...items.map((c) => h("option", { value: c.case_id },
    `${c.case_id.slice(0, 8)} · ${label(T.STATES, c.workflow_state)}${inReview.has(c.case_id) ? " · revisión abierta" : ""}`)));
  if (!items.length) {
    $("case").replaceChildren(h("p", { className: "muted" }, "No tienes casos asignados."));
    return;
  }
  const ids = items.map((c) => c.case_id);
  const first = open.find((i) => ids.includes(i.case_id));
  await selectCase(ids.includes(state.caseId) ? state.caseId : (first ? first.case_id : ids[0]));
}

async function selectCase(caseId) {
  state.caseId = caseId;
  $("case-select").value = caseId;
  const [res, events] = await Promise.all([
    api("GET", `/cases/${caseId}`), api("GET", `/cases/${caseId}/events`),
  ]);
  if (!res.ok) {
    $("case").replaceChildren(h("div", { className: "banner err" }, res.code === "RATE_LIMITED" ? T.ERRORS.RATE_LIMITED : "No se pudo cargar el caso."));
    return;
  }
  state.snap = res.body;
  state.events = events.ok ? events.body.items : [];
  rerender();
}

// ---------- detalle del caso ----------

const rerender = () => renderCase(state.snap, state.events);

function renderCase(s, events) {
  const parts = [
    h("p", {}, h("strong", {}, "Estado: "), label(T.STATES, s.workflow_state),
      ` · versión ${s.case_version} · revisión de datos ${s.input_revision}`),
    h("div", { className: "steps" }, s.progress.map((p) => h("span", { className: `step ${p.status}` }, label(T.STAGES, p.stage)))),
    s.open_review ? reviewPanel(s) : statusBanner(s),
    creditSummary(s),
    h("h3", {}, "Datos declarados"),
    declared(s),
  ];
  if (s.key_quote) parts.push(h("p", { className: "muted" }, `Cotización de llave: ${money(s.key_quote.amount)}, vigente hasta ${s.key_quote.expires_at.slice(0, 10)}.`));
  if (s.offers && s.offers.length) {
    parts.push(h("h3", {}, "Ofertas vigentes"), table(
      ["Plazo", "Efectivo", "Llave", "Capital financiado", "Tasa", "Cuota", "Último pago", "Total"],
      s.offers.map((o) => [`${o.term_months} meses`, money(o.cash_amount), money(o.key_cost), money(o.financed_principal),
        rate(o.annual_nominal_rate), money(o.regular_payment), money(o.last_payment), money(o.total_payment)])));
  }
  if (s.selection) parts.push(h("p", { className: "muted" }, `Oferta elegida por el cliente: ${s.selection.offer_id.slice(0, 8)}.`));
  if (s.documents && s.documents.length) parts.push(h("h3", {}, "Documentos"), ...s.documents.map((d) => documentRow(s, d)));
  if (s.validations && s.validations.length) {
    parts.push(h("h3", {}, "Validaciones"), table(["Regla", "Resultado", "Motivo", "Campo"],
      s.validations.map((v) => [v.rule_id, v.status, v.reason_code || "—", v.refs.map((r) => r.field || "").join(", ")])));
  }
  if (s.operations && s.operations.length) {
    parts.push(h("h3", {}, "Operaciones con proveedores"), table(["Operación", "Estado", "Intentos", "Referencia"],
      s.operations.map((o) => [label(T.OPERATIONS, o.action), o.status, o.attempts, o.provider_ref || "—"])));
  }
  if (s.reviews && s.reviews.length) {
    parts.push(h("h3", {}, "Historial de revisiones"), table(["Motivo", "Estado", "Resolución", "Abierta"],
      s.reviews.map((r) => [label(T.REVIEW_REASONS, r.reason_code), r.status, T.RESOLUTIONS[r.resolution] || "—", when(r.opened_at)])));
  }
  if (s.ready) parts.push(h("p", { className: "muted" }, `Dictamen final: huella ${s.ready.fingerprint.slice(0, 16)}…`));
  parts.push(h("h3", {}, "Eventos de auditoría"), table(["Versión", "Evento", "Detalle", "Fecha"],
    events.map((e) => [e.case_version, e.event_type, e.payload == null ? "—" : JSON.stringify(e.payload), when(e.created_at)])));
  $("case").replaceChildren(...parts);
}

function statusBanner(s) {
  const elig = s.eligibility || {};
  if (s.workflow_state === "REJECTED") {
    return h("div", { className: "banner err" }, "No se puede continuar: ",
      (elig.rejection_reasons || []).map((r) => label(T.REJECTIONS, r)).join(" "));
  }
  if (s.workflow_state === "READY_FOR_FINANCIAL") {
    return h("div", { className: "banner ok" }, "Expediente listo para revisión de la financiera. Pasó la verificación final; no es una aprobación de crédito ni un desembolso.");
  }
  const next = s.next_action || {};
  if (next.type === "CORRECT_REQUESTED") {
    return h("div", { className: "banner warn" }, `Corrección pedida al cliente: ${next.targets.map((t) => label(T.CORRECTION_TARGETS, t).toLowerCase()).join(", ")}. ${next.message}`);
  }
  if (next.type === "CORRECT_DOCUMENTS") {
    return h("div", { className: "banner warn" }, "Corrección documental pendiente: ", next.reasons.map(correctionText).join(" "));
  }
  return null;
}

function creditSummary(s) {
  const p = s.credit_profile;
  if (!p) return null;
  const text = p.outcome === "OK"
    ? `Historial consultado. Tasa: ${rate(p.annual_nominal_rate)}; monto máximo financiable: ${money(p.max_financed_principal)}.`
    : "Historial consultado: requiere revisión de un asesor.";
  return h("p", { className: "muted" }, `${text} Score: ${p.score ?? "—"} · Banda: ${p.band || "—"} · ${p.reason_code}`);
}

function declared(s) {
  const rows = [];
  for (const group of ["vehicle", "profile"]) {
    for (const [name, value] of Object.entries(s[group] || {})) {
      if (value !== null) rows.push([label(T.FIELDS, name), describe(name, value)]);
    }
  }
  return rows.length ? table(["Dato", "Valor"], rows) : h("p", { className: "muted" }, "Aún no hay datos confirmados.");
}

function documentRow(s, d) {
  const download = h("button", { className: "link", type: "button", onclick: () => downloadDocument(s.case_id, d) }, "Descargar");
  const row = h("p", {}, `${label(T.DOCUMENT_KINDS, d.kind)} · versión ${d.revision} · ${label(T.DOCUMENT_STATUS, d.status)} · `, download);
  if (!d.extraction) return row;
  const fields = Object.entries(d.extraction.fields).map(([name, f]) => [
    label(T.DOCUMENT_FIELDS, name), f.value == null ? "—" : String(f.value),
    f.confidence == null ? "—" : Number(f.confidence).toFixed(2), f.human_verified ? "Sí" : "No",
  ]);
  return h("div", {}, row, h("details", {}, h("summary", {}, `Lectura de ${label(T.DOCUMENT_KINDS, d.kind).toLowerCase()}`),
    table(["Campo", "Valor leído", "Confianza", "Lectura humana"], fields)));
}

async function downloadDocument(caseId, d) {
  const res = await fetch(`/cases/${caseId}/documents/${d.document_id}/content`, { headers: { Authorization: `Bearer ${state.token}` } });
  if (!res.ok) { flash("err", "No se pudo descargar el documento."); return; }
  const ext = { "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg" }[d.mime_type] || "bin";
  const url = URL.createObjectURL(await res.blob());
  const a = h("a", { href: url, download: `${d.kind.toLowerCase()}-${d.revision}.${ext}` });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------- revisión humana (TDD §6.8): ninguna resolución marca el caso como listo ----------

function reviewPanel(s) {
  const review = s.open_review;
  const failed = (s.validations || []).filter((v) => v.status !== "PASS");
  const names = ["Solicitar corrección", "Registrar lectura", "Conciliar operación", "Reanudar"];
  const bodies = [correctionForm, amendForm, reconcileForm, resumeForm];
  const panel = h("div", {});
  const tabs = h("div", { className: "tabs", role: "tablist" }, names.map((n, i) => h("button", {
    type: "button", role: "tab", "aria-selected": String(i === state.tab),
    onclick: () => { state.tab = i; rerender(); },
  }, n)));
  panel.append(bodies[state.tab](s));
  return h("div", { className: "banner warn" },
    h("strong", {}, "Revisión abierta: "), label(T.REVIEW_REASONS, review.reason_code),
    h("div", { className: "muted" }, `Etapa previa: ${label(T.STATES, review.resume_state)} · abierta ${when(review.opened_at)}`),
    failed.length ? h("div", { className: "muted" }, "Validaciones con problema: ", failed.map(correctionText).join("; ")) : null,
    tabs, panel);
}

async function submit(button, body, success) {
  button.disabled = true;
  const s = state.snap;
  let res;
  try {
    res = await api("POST", `/cases/${s.case_id}/reviews/${s.open_review.review_id}/resolutions`, { body, version: s.case_version });
  } catch {
    res = { ok: false, body: { message: "No se pudo contactar a la API. Intenta de nuevo." } };
  } finally {
    button.disabled = false;
  }
  if (res.ok) {
    flash("ok", success || "Resolución registrada.");
    state.tab = 0;
    state.providerStatus = {};
  } else {
    flash(res.code === "VERSION_CONFLICT" ? "warn" : "err", errorText(res));
  }
  await refresh();
}

function correctionForm() {
  const checks = Object.entries(T.CORRECTION_TARGETS).map(([code, text]) => h("label", {}, h("input", { type: "checkbox", value: code }), text));
  const message = h("textarea", { id: "correction-message", maxlength: "500", rows: "3" });
  const button = h("button", { type: "button" }, "Solicitar corrección");
  button.addEventListener("click", () => submit(button, {
    resolution: "REQUEST_CORRECTION",
    targets: checks.map((l) => l.firstChild).filter((c) => c.checked).map((c) => c.value),
    message: message.value.trim(),
  }));
  return h("div", {}, h("label", {}, "Qué debe corregir el cliente"), h("div", { className: "checks" }, checks),
    h("label", { for: "correction-message" }, "Mensaje para el cliente"), message, button);
}

function amendForm(s) {
  const docs = (s.documents || []).filter((d) => d.extraction);
  if (!docs.length) return h("p", { className: "muted" }, "No hay documentos leídos en este caso.");
  const box = h("div", {});
  const select = h("select", { id: "amend-document", onchange: () => fill() },
    docs.map((d) => h("option", { value: d.document_id }, label(T.DOCUMENT_KINDS, d.kind))));
  const reason = h("input", { id: "amend-reason", type: "text" });
  const button = h("button", { type: "button" }, "Registrar lectura humana");
  function fill() {
    const doc = docs.find((d) => d.document_id === select.value);
    box.replaceChildren(...Object.entries(doc.extraction.fields).map(([name, f]) => {
      const conf = f.confidence == null ? "—" : Number(f.confidence).toFixed(2);
      const use = h("input", { type: "checkbox", "data-field": name });
      const input = h("input", { type: "text", value: f.value == null ? "" : String(f.value), "data-value": name, "aria-label": label(T.DOCUMENT_FIELDS, name) });
      return h("div", { className: "checks" }, h("label", {}, use, `${label(T.DOCUMENT_FIELDS, name)} (confianza ${conf})`), input);
    }));
  }
  fill();
  button.addEventListener("click", () => {
    const fields = {};
    for (const use of box.querySelectorAll("input[data-field]")) {
      if (!use.checked) continue;
      const value = box.querySelector(`input[data-value="${use.dataset.field}"]`).value.trim();
      fields[use.dataset.field] = value || null;
    }
    submit(button, { resolution: "AMEND_EXTRACTION", document_id: select.value, fields, reason: reason.value.trim() });
  });
  return h("div", {}, h("label", { for: "amend-document" }, "Documento"), select,
    h("label", {}, "Campos que leíste en el documento (marca los que registras)"), box,
    h("label", { for: "amend-reason" }, "Motivo de la lectura"), reason,
    h("p", { className: "muted" }, "La lectura humana sustituye solo el requisito de confianza de esos campos; las comparaciones con lo declarado se siguen aplicando."),
    button);
}

function reconcileForm(s) {
  const unknown = (s.operations || []).filter((o) => o.status === "UNKNOWN");
  if (!unknown.length) return h("p", { className: "muted" }, "No hay operaciones con resultado incierto.");
  return h("div", {}, unknown.map((op) => {
    const id = op.operation_id;
    const status = state.providerStatus[id];
    const lookup = h("button", { type: "button" }, "Consultar al proveedor");
    lookup.addEventListener("click", async () => {
      lookup.disabled = true;
      const res = await api("GET", `/cases/${s.case_id}/operations/${id}/provider-status`);
      lookup.disabled = false;
      if (res.ok) state.providerStatus[id] = res.body; else flash("err", errorText(res));
      rerender();
    });
    const body = { resolution: "RECONCILE_OPERATION", operation_id: id };
    let decision = null;
    if (status && status.effect_found) {
      const b = h("button", { type: "button" }, "Confirmar efecto y aplicar resultado");
      b.addEventListener("click", () => submit(b, { ...body, outcome: "EFFECT_CONFIRMED", provider_ref: status.provider_ref }));
      decision = h("div", {}, h("p", {}, `El proveedor registra el efecto. Referencia: ${status.provider_ref}`), b);
    } else if (status) {
      const b = h("button", { type: "button" }, "Confirmar sin efecto y reintentar");
      b.addEventListener("click", () => submit(b, { ...body, outcome: "NO_EFFECT" }));
      decision = h("div", {}, h("p", {}, "El proveedor no registra ningún efecto para esta operación."), b);
    }
    return h("div", {}, h("p", {}, `${label(T.OPERATIONS, op.action)} · ${op.attempts} intentos`), lookup, decision);
  }));
}

function resumeForm() {
  const button = h("button", { type: "button" }, "Reanudar");
  button.addEventListener("click", () => submit(button, { resolution: "RESUME" }));
  return h("div", {}, h("p", { className: "muted" },
    "Reanuda solo si el motivo quedó resuelto. Las reglas y la verificación final se vuelven a ejecutar; esta acción nunca marca el caso como listo por sí sola."), button);
}

// ---------- arranque ----------

$("enter").addEventListener("click", () => enter($("token").value));
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") enter($("token").value); });
$("logout").addEventListener("click", logout);
$("case-select").addEventListener("change", (e) => { state.tab = 0; state.providerStatus = {}; selectCase(e.target.value); });
try {
  const saved = sessionStorage.getItem("advisorToken");
  if (saved) enter(saved);
} catch { /* sin almacenamiento */ }
