# Revisión manual de dos trazas de cliente (TDD §8.2, «Experiencia»)

> **Seguimiento 28/09/2026:** se revisó un happy path real del usuario y se implementaron controles de lenguaje y progreso del chat. La [evidencia consolidada y sus límites](../docs/auto_equity_TDD.md#84-evidencia-y-limitaciones) distingue los ensayos posteriores. Este informe histórico no se considera una nueva revisión firmada.

> **Obsoleta desde el cambio de arquitectura del agente:** revisa respuestas del agente interno, que se retiró. Antes de firmar hay que repetirla sobre dos hilos reales del agente de la conversación (con un modelo real, porque el texto lo redacta el modelo). El método y los criterios de abajo siguen sirviendo.
>
> **BORRADOR, pendiente de firma.** Lo preparó el agente de desarrollo con el método de abajo. La persona que firma debe leerlo, repetir lo que quiera verificar y confirmar o corregir cada respuesta.

**Trazas:** [`reports/traces/happy_path_sin_llave/`](traces/happy_path_sin_llave/) (escenario D04) y [`reports/traces/correccion_documental/`](traces/correccion_documental/) (D05), generadas sobre `2174312`.

**Método:** las trazas no llevan el texto de la conversación, a propósito. Para ver lo que el asistente le dice al cliente, se repitieron los dos escenarios contra la API con el perfil evaluador (decisor *scripted*, sin costo) y se copió la respuesta textual.

## Traza 1: happy path sin segunda llave (D04)

Recorrido: auto (sin segunda llave, con placa y datos del auto) → perfil → Buró → ofertas → «¿Qué opciones tengo?» → elección de 24 meses → 3 documentos → `READY_FOR_FINANCIAL`. La traza tiene 18 spans: 12 `http.request`, 3 `document.extraction` y 1 turno del agente (`explain_status`).

Respuesta del asistente a «¿Qué opciones tengo?»:

> Tus opciones: 12 meses: cuota de $5,011.66 MXN, total $60,139.92 MXN; 24 meses: cuota de $2,802.17 MXN, total $67,252.02 MXN. Incluye $3,000.00 MXN de la segunda llave. Elige una con su botón.

| Criterio | Resultado | Evidencia |
| --- | --- | --- |
| ¿Preguntas redundantes? | **No** | Un solo turno; no vuelve a pedir datos ya confirmados |
| ¿Correcciones sin documento o dato concreto? | **No aplica** | No hubo corrección |
| ¿Cifras no sustentadas? | **No** | Cuota, total y llave coinciden al centavo con la oferta calculada (valores de §6.5, verificados por D04) |

## Traza 2: corrección documental (D05)

Recorrido: happy path con `payslip_ana_low.pdf` → `NEEDS_CORRECTION` por `INCOME_MATCH` (ingreso fuera de la tolerancia del 10 %) → «¿Qué sigue?». La traza tiene 17 spans: 11 `http.request`, 3 `document.extraction` y 1 turno del agente (`explain_status`).

Respuesta del asistente a «¿Qué sigue?»:

> Necesitamos una corrección. El ingreso del comprobante no coincide con el declarado (tolerancia del 10 %). Revisa el monto y la periodicidad que declaraste, o carga un comprobante que los sustente. Ronda de corrección 1 de 2.

| Criterio | Resultado | Evidencia |
| --- | --- | --- |
| ¿Preguntas redundantes? | **No** | Un solo turno |
| ¿Correcciones sin documento o dato concreto? | **No, tras una corrección** (ver hallazgo) | Nombra el documento (comprobante), el dato (ingreso), la regla (10 %) y la ronda |
| ¿Cifras no sustentadas? | **No** | No da cifras; la tolerancia es la de la política |

**Hallazgo corregido durante la revisión:** antes, la respuesta era «Necesitamos corregir un documento; revisa las instrucciones en tu solicitud», sin decir qué documento ni qué dato. Incumplía este criterio en el chat, aunque la tarjeta de corrección de la pantalla sí lo decía. Ahora el chat usa los mismos textos que la tarjeta, y el escenario D05 exige que se nombre el documento (37/37 PASS).

## Conclusión

Con el decisor *scripted*, las dos trazas cumplen los tres criterios después de la corrección. **Limitación:** con un modelo real, el texto lo redacta el modelo sobre las mismas plantillas y cifras. Conviene repetir esta revisión con las trazas de la pista de IA real cuando se ejecute.

**Revisó:** ______________________ · **Fecha:** ____________ · **Firma:** ______________________
