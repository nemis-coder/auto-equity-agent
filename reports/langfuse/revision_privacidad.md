# Revisión de privacidad de las trazas en Langfuse

**Fecha:** 2026-09-25 · **Entorno:** `local` (Langfuse Cloud del usuario) · **Código:** `ae6eb33`

## Método

Consulta de solo lectura a la API v2 de Langfuse (`/api/public/v2/observations`, con metadatos, uso, modelo e input/output) de todas las observaciones de los últimos dos días. En el JSON completo se buscaron:
- los datos de la demo: nombre, calle, colonia, placa (con y sin guiones), empleador y código postal;
- los cuatro tokens de los actores.

## Resultado

| Qué | Resultado |
| --- | --- |
| Observaciones revisadas | 2,375 (`http.request` 2,296; `document.extraction` 62; `agent.turn`, `agent.decision` y `agent.tool` 5 cada una; traza de humo 2) |
| Datos personales o tokens encontrados | **Ninguno** |
| Observaciones con input u output no vacío | **0** (solo metadatos sanitizados) |
| `document.extraction` con Claude real | 10 (proveedor, modelo, versión del prompt, resultado y tokens) |
| `agent.decision` con Claude real (agente interno retirado; [arquitectura vigente](../../docs/auto_equity_TDD.md#4-arquitectura); hoy cada llamada del agente se traza como `agent.turn`, también sin texto) | 2 turnos: `explain_status` (1,718/103 tokens) y `propose_declarations` con placa (1,927/224) |

## Después de la revisión

- 52 `document.extraction` y la mayoría de los `http.request` vienen de las pruebas automáticas, que se corrieron una vez con Langfuse activo (`provider: fake`). No tenían datos personales, pero ensuciaban las métricas. **Después de esta revisión se borraron todas las trazas del proyecto** (2,298, por `DELETE /api/public/traces`); una consulta posterior devolvió 0 observaciones.
- La revisión manual de dos trazas de cliente (TDD §8.2) es otra tarea: evalúa la experiencia, no la privacidad (`reports/revision_manual_trazas.md`).
