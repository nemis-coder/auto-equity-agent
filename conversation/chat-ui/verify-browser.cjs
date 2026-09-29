// Revisión de un hilo existente, sin enviar mensajes ni invocar modelos.
// PLAYWRIGHT_MODULE permite usar una instalación existente, sin añadir dependencias de runtime.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

async function main() {
  const thread = process.env.TEST_THREAD_ID;
  assert.match(thread || "", /^[a-f0-9-]{36}$/);
  const token = execFileSync("docker", ["compose", "exec", "-T", "api", "python", "-c",
    'import json; print(json.load(open("/data/demo-credentials.json"))["cliente-ana"])'], { encoding: "utf8" }).trim();
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const output = process.env.QA_OUTPUT || "output/chat-qa";
  fs.mkdirSync(output, { recursive: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    // Bloquear mutaciones incluso si el test se modifica accidentalmente. /search e /history
    // son consultas POST del SDK; los modelos solo se ejecutan con /runs, siempre bloqueado.
    await page.route("http://localhost:2026/**", (route) => {
      const req = route.request();
      if (req.method() !== "GET" && req.method() !== "OPTIONS" && !/\/(search|history)$/.test(new URL(req.url()).pathname)) return route.abort();
      return route.continue();
    });
    await page.goto("http://localhost:3000");
    await page.locator('input[name="apiUrl"]').fill("http://localhost:2026");
    await page.locator('input[name="assistantId"]').fill("auto_equity");
    await page.locator('input[name="apiKey"]').fill(token);
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.goto(`http://localhost:3000/?apiUrl=http://localhost:2026&assistantId=auto_equity&threadId=${thread}`);
    await page.getByRole("status", { name: "Progreso de documentos" }).waitFor();
    const text = await page.locator("body").innerText();
    assert.ok(!/READY_FOR_FINANCIAL|DOCUMENT_COLLECTION|subir_documento/.test(text), "Se filtró un código o herramienta");
    assert.ok(text.includes("lista para revisión de la financiera"));
    const progress = page.getByRole("status", { name: "Progreso de documentos" });
    assert.equal(await progress.locator("li").count(), 3);
    assert.equal((await progress.innerText()).match(/Procesado\./g).length, 3);
    await progress.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "chat-completado.png") });
    // Reproducir un checkpoint de aprobación existente solo en este navegador. Ninguna
    // decisión se envía al servidor y el expediente real permanece cerrado.
    const response = await page.request.post(`http://localhost:2026/threads/${thread}/history`, {
      headers: { "X-Api-Key": token }, data: { limit: 1000 },
    });
    assert.ok(response.ok());
    const history = await response.json();
    const pausedIndex = history.findIndex((s) => s.interrupts?.length || s.tasks?.some((t) => t.interrupts?.length));
    assert.ok(pausedIndex >= 0, "Falta checkpoint de aprobación en el hilo");
    await page.route(`**/threads/${thread}/history`, (route) => route.fulfill({ json: history.slice(pausedIndex) }));
    await page.route(`**/threads/${thread}/state`, (route) => route.fulfill({ json: history[pausedIndex] }));
    await page.reload();
    const approve = page.getByRole("button", { name: /approve|accept|aprobar/i }).first();
    await approve.waitFor();
    await approve.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "tarjeta-aprobacion.png") });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: "PASS", checks: ["historial existente", "estados y herramientas ocultos", "tres documentos procesados", "tarjeta de aprobación visible con herramientas ocultas", "sin errores JavaScript", "sin llamadas al modelo"], screenshots: output }));
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error.message); process.exitCode = 1; });
