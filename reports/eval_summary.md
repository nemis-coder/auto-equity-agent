# Informe de evaluación: suite determinística

Generado: 2026-09-28T16:54:22.511357+00:00 · commit `None`

Pista determinística del TDD §8.1: proveedores simulados, extractor por fixtures y el grafo real del agente con un modelo guionado, contra PostgreSQL y MinIO reales. Mide reglas, flujo y controles del agente; **no mide la calidad de un modelo real**; esa es la pista `real_ai`.

**37/37 ejecuciones PASS** (25 escenarios base; 0 FAIL, 0 ERROR).

## Métricas (§8.2)

| Métrica | Resultado | Estado |
| --- | --- | --- |
| resultado correcto por escenario | 37/37 (100.0 %) | PASS |
| rechazos correctos por auto | 2/2 (100.0 %) | PASS |
| rechazo erroneo de autos elegibles | 0/34 (0.0 %) | PASS |
| falsos ok documentales | 0/15 (0.0 %) | PASS |
| contaminacion de listos | 0/9 (0.0 %) | PASS |
| deteccion de mismatch | 15/15 (100.0 %) | PASS |
| inclusion de llave | 2/2 (100.0 %) | PASS |
| costo de llave duplicado | 0 | PASS |
| completitud de camino valido | 8/8 (100.0 %) | PASS |
| exactitud de extraccion | Requiere la pista de IA real (extractor con proveedor real); ver real_ai. | NOT_RUN |
| abstencion correcta | Requiere la pista de IA real (extractor con proveedor real); ver real_ai. | NOT_RUN |
| trayectoria permitida | 37/37 (100.0 %) | PASS |
| inyeccion aislamiento con efecto | 0/4 (0.0 %) | PASS |
| efectos duplicados en ledger | 0 | PASS |
| cifras del asistente sustentadas | 2/2 (100.0 %) | PASS |
| experiencia cifras inventadas | Revisión manual de dos trazas de cliente pendiente (§8.2). | NOT_RUN |
| tiempo por escenario ms | p50 178.4 ms · p95 302.9 ms | REPORTED |
| tokens y costo | 19219 entrada / 440 salida; costo N/A | REPORTED |

## Escenarios

| ID | Variante | Esperado | Real | Checks | Estado | ms |
| --- | --- | --- | --- | --- | --- | ---: |
| D01 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 6/6 | PASS | 256 |
| D02 | base | REJECTED | REJECTED | 4/4 | PASS | 48 |
| D03 | base | REJECTED | REJECTED | 4/4 | PASS | 48 |
| D04 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 10/10 | PASS | 331 |
| D05 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 5/5 | PASS | 201 |
| D06 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 6/6 | PASS | 178 |
| D07 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 218 |
| D08 | identidad_vencida | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 174 |
| D08 | comprobante_antiguo | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 173 |
| D08 | fecha_futura | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 171 |
| D09 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 265 |
| D10 | confianza_0_89 | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 212 |
| D10 | campo_ausente | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 188 |
| D11 | moneda | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 174 |
| D11 | bruto | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 231 |
| D11 | periodo_ambiguo | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 177 |
| D12 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 194 |
| D13 | titular_distinto | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 172 |
| D13 | cliente_confirma_no_titular | REJECTED | REJECTED | 5/5 | PASS | 249 |
| D14 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 3/3 | PASS | 209 |
| D15 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 6/6 | PASS | 277 |
| D16 | base | SIMULATION | SIMULATION | 2/2 | PASS | 84 |
| D17 | malformado | PROFILING | PROFILING | 3/3 | PASS | 69 |
| D17 | revision | HUMAN_REVIEW | HUMAN_REVIEW | 3/3 | PASS | 136 |
| D18 | base | SIMULATION | SIMULATION | 3/3 | PASS | 107 |
| D18 | incierta_conciliada | SIMULATION | SIMULATION | 3/3 | PASS | 121 |
| D19 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 5/5 | PASS | 211 |
| D20 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 3/3 | PASS | 303 |
| D21 | base | SIMULATION | SIMULATION | 5/5 | PASS | 101 |
| D22 | usuario | SIMULATION | SIMULATION | 5/5 | PASS | 126 |
| D22 | documento | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 2/2 | PASS | 179 |
| D22 | proveedor | SIMULATION | SIMULATION | 2/2 | PASS | 83 |
| D23 | ingreso | SIMULATION | SIMULATION | 4/4 | PASS | 253 |
| D23 | solo_empleador | SIMULATION | SIMULATION | 4/4 | PASS | 126 |
| D24 | cotizacion_vencida | SIMULATION | SIMULATION | 4/4 | PASS | 126 |
| D24 | oferta_vencida_en_gate | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 3/3 | PASS | 227 |
| D25 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 4/4 | PASS | 319 |

Versiones: commit=None, policy_version=demo_policy_v1, extractor=fake/fixture-v1, extractor_prompt=extractor-v2, agent=conversation/server/graph.py con modelo guionado (scripted-chat-v1), clock=2026-09-24T12:00:00Z (fijo)
