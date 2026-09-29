# Verificación de la simplificación — 28 de septiembre de 2026

## Resultado

Las verificaciones ejecutadas pasan. API y agente locales quedaron actualizados con las imágenes simplificadas; no se modificó `.env`, no se borraron volúmenes y no se migraron datos del entorno principal.

Código probado: base `63d7ec2` con cambios locales de 0039, identificados en la evaluación como `63d7ec2-dirty-33cfa11cf2ed`. **No es un commit nuevo.** SHA-256 del diff de `app`, `conversation/server`, `pyproject.toml`, `requirements` y `docker-compose.yml`: `33cfa11cf2eda1e5180ca1890516a6065171ff073b45aacc08a005ca68fe0e98`.

| Comprobación | Resultado |
| --- | --- |
| API, dominio, seguridad e integración (`pytest tests -q`) | 316/316 PASS |
| Grafo conversacional (`unittest test_graph`) | 30/30 PASS |
| Navegador Chromium: pantalla del asesor, resolución humana y controles de acceso | 3/3 PASS |
| Suite determinística: 25 escenarios base con variantes | 37/37 PASS |
| Demos HTTP: camino feliz, rechazo, fallo documental y falta de llave | 4/4 OK |
| Ruff | Sin errores |
| API principal `/readyz` | HTTP 200, PostgreSQL y MinIO disponibles |
| Pantalla `/asesor` y página del chat | HTTP 200 |
| Aegra: cliente autenticado consulta el grafo `auto_equity` | OK |
| Aegra: sin token y token de asesor | Ambos bloqueados con 401 |
| CORS del chat desde `http://localhost:3000` | OK |

## Evidencia de negocio

- Camino feliz: `READY_FOR_FINANCIAL`, que no equivale a aprobar o desembolsar el crédito.
- Auto no propio: `REJECTED` por `VEHICLE_NOT_OWNED`.
- Comprobante con ingreso distinto: `NEEDS_CORRECTION` por `INCOME_MISMATCH`.
- Sin segunda llave: costo de $3,000 incluido una sola vez; capital $53,000, cuota $2,802.17 y último pago $2,802.11 a 24 meses. Termina listo.
- Evaluación: 0/15 falsos OK documentales, 15/15 discrepancias detectadas, 0 costos de llave duplicados, 0 efectos duplicados en el ledger y 0/4 ataques con efecto.

[Informe completo](evaluation/eval_summary.md), [resultados JSON](evaluation/eval_summary.json) y [captura del asesor después de resolver D15](screenshots/asesor_resolucion_d15.png).

## Alcance y límites

Las demos, la evaluación y el navegador se ejecutaron en el proyecto Compose aislado `auto-equity-verify`, con sus propios volúmenes, extractor fake y Langfuse desactivado. Las pruebas de integración usan la base reservada `_test`. No se usaron expedientes del usuario para simular recorridos.

La comprobación del entorno principal fue de disponibilidad, configuración, autenticación y CORS, sin ejecutar llamadas al modelo. Mantiene Anthropic como extractor y Langfuse configurado; el readiness no demuestra por sí solo recepción de trazas en Langfuse.

**No se ejecutó una conversación ni extracción con Anthropic real en esta verificación.** El modelo guionado comprueba flujo y controles, no calidad conversacional ni disponibilidad de la cuenta del proveedor. La prueba con costo queda pendiente de confirmación.

El runner de navegador emitió cuatro advertencias de opciones de pytest desconocidas (opciones de asyncio y cache); las tres pruebas terminaron correctamente. No se modificó código de aplicación durante esta verificación.

## Estado al terminar

- Entorno principal: API y agente actualizados, servicios activos; datos y volúmenes conservados.
- Entorno aislado de verificación: detenido después de guardar la evidencia, con volúmenes conservados.
- Los informes anteriores no se sobrescribieron. Los cambios de implementación siguen locales, sin commit ni push.
