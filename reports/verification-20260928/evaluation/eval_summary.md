# Informe de evaluación: suite determinística

Generado: 2026-09-28T21:27:25.032354+00:00 · commit `63d7ec2-dirty-33cfa11cf2ed`

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
| tiempo por escenario ms | p50 191.1 ms · p95 289.6 ms | REPORTED |
| tokens y costo | 18956 entrada / 440 salida; costo N/A | REPORTED |

## Escenarios

| ID | Variante | Esperado | Real | Checks | Estado | ms |
| --- | --- | --- | --- | --- | --- | ---: |
| D01 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 6/6 | PASS | 227 |
| D02 | base | REJECTED | REJECTED | 4/4 | PASS | 48 |
| D03 | base | REJECTED | REJECTED | 4/4 | PASS | 47 |
| D04 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 10/10 | PASS | 338 |
| D05 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 5/5 | PASS | 203 |
| D06 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 6/6 | PASS | 194 |
| D07 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 175 |
| D08 | identidad_vencida | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 232 |
| D08 | comprobante_antiguo | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 191 |
| D08 | fecha_futura | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 182 |
| D09 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 173 |
| D10 | confianza_0_89 | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 257 |
| D10 | campo_ausente | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 174 |
| D11 | moneda | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 220 |
| D11 | bruto | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 189 |
| D11 | periodo_ambiguo | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 182 |
| D12 | base | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 244 |
| D13 | titular_distinto | NEEDS_CORRECTION | NEEDS_CORRECTION | 4/4 | PASS | 181 |
| D13 | cliente_confirma_no_titular | REJECTED | REJECTED | 5/5 | PASS | 222 |
| D14 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 3/3 | PASS | 217 |
| D15 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 6/6 | PASS | 277 |
| D16 | base | SIMULATION | SIMULATION | 2/2 | PASS | 85 |
| D17 | malformado | PROFILING | PROFILING | 3/3 | PASS | 154 |
| D17 | revision | HUMAN_REVIEW | HUMAN_REVIEW | 3/3 | PASS | 72 |
| D18 | base | SIMULATION | SIMULATION | 3/3 | PASS | 104 |
| D18 | incierta_conciliada | SIMULATION | SIMULATION | 3/3 | PASS | 132 |
| D19 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 5/5 | PASS | 208 |
| D20 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 3/3 | PASS | 331 |
| D21 | base | SIMULATION | SIMULATION | 5/5 | PASS | 152 |
| D22 | usuario | SIMULATION | SIMULATION | 5/5 | PASS | 170 |
| D22 | documento | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 2/2 | PASS | 223 |
| D22 | proveedor | SIMULATION | SIMULATION | 2/2 | PASS | 113 |
| D23 | ingreso | SIMULATION | SIMULATION | 4/4 | PASS | 254 |
| D23 | solo_empleador | SIMULATION | SIMULATION | 4/4 | PASS | 127 |
| D24 | cotizacion_vencida | SIMULATION | SIMULATION | 4/4 | PASS | 229 |
| D24 | oferta_vencida_en_gate | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 3/3 | PASS | 281 |
| D25 | base | READY_FOR_FINANCIAL | READY_FOR_FINANCIAL | 4/4 | PASS | 290 |

Versiones: commit=63d7ec2-dirty-33cfa11cf2ed, policy_version=demo_policy_v1, extractor=fake/fixture-v1, extractor_prompt=extractor-v2, agent=conversation/server/graph.py con modelo guionado (scripted-chat-v1), clock=2026-09-24T12:00:00Z (fijo)
