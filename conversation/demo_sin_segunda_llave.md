# Demo: sin segunda llave, con el costo incorporado al plan

**Objetivo:** comprobar que no tener segunda llave permite continuar y que su reposición
se cotiza y financia una sola vez, sin descontarla del efectivo elegido por el cliente.

Este es un guion con datos sintéticos, no una conversación ya ejecutada. Envía una
respuesta por turno; las preguntas del agente pueden variar.

## Antes de empezar

Sigue el [arranque y acceso del README](../README.md#arranque-rápido), entra como
`cliente-ana` y abre **un hilo nuevo**. Conserva la política y los cotizadores de ejemplo,
`EXTRACTION_PROVIDER=fake` y el reloj de negocio de `.env.example` para reproducir este caso.
El chat usa IA real y tiene costo. Con extracción real también hay costo y puede variar
el resultado documental.

## 1. Iniciar y declarar que falta la segunda llave

```text
Hola, quiero solicitar un crédito con garantía de mi auto.
```

Responde a cada pregunta, por separado:

| Dato solicitado | Respuesta de Ana: un mensaje |
| --- | --- |
| Titularidad | Sí, el auto está a mi nombre. |
| Adeudos o gravámenes | No, no tiene adeudos ni gravámenes que impidan usarlo como garantía. |
| Segunda llave | No, no tengo la segunda llave. |
| Marca | Es un Nissan. |
| Modelo | Es un Versa. |
| Año | Es del año 2020. |
| Placa | La placa es ABC-123-XYZ. |

**Primera tarjeta:** comprueba especialmente **segunda llave: no** y aprueba si los
demás datos coinciden. El agente debe explicar que se cotizará la reposición, no rechazar
la solicitud por ese motivo. No le indiques tú un precio: debe obtenerlo del sistema.

## 2. Completar el perfil

| Dato solicitado | Respuesta de Ana: un mensaje |
| --- | --- |
| Nombre completo | Mi nombre completo es Ana Prueba López. |
| Domicilio | Mi domicilio es Calle Demo, número exterior 123, sin número interior, Colonia Ejemplo, municipio Ciudad de México, estado CDMX, código postal 00000, México. |
| Tipo de empleo | Soy asalariada. |
| Empresa | Trabajo en Empresa Sintética. |
| Ingreso neto | Mi ingreso neto es de 20,000 pesos mexicanos, después de impuestos. |
| Periodicidad | Ese importe corresponde a mi ingreso mensual. |

Si el domicilio se pide por partes, usa las
[respuestas por campo del guion de Ana](demo_ana.md#3-responder-los-datos-personales-y-de-ingreso).
**Segunda tarjeta:** revisa y aprueba el perfil con ingreso neto mensual de 20,000 MXN.

## 3. Comprobar la cotización y elegir la oferta

Espera a que el sistema cotice la llave y el agente presente las ofertas. Con la
configuración sintética de esta demo, la reposición cuesta **3,000 MXN**.

Si aparece la opción de 50,000 MXN de efectivo a 24 meses, envía:

```text
Elijo la opción de 50,000 pesos mexicanos de efectivo a 24 meses.
```

**Tercera tarjeta:** contrasta sus cifras con la oferta presentada antes de aprobar.
Para esta configuración, la referencia es:

| Concepto | Importe o valor esperado |
| --- | --- |
| Efectivo elegido por el cliente | 50,000.00 MXN |
| Reposición de la segunda llave | 3,000.00 MXN |
| Capital financiado: efectivo + llave | 53,000.00 MXN |
| Plazo | 24 meses |
| Cuota regular | 2,802.17 MXN |
| Último pago | 2,802.11 MXN |
| Total de pagos | 67,252.02 MXN |

Estos valores corresponden al perfil y a la tarifa de ejemplo; no son una oferta
comercial. Las cuotas se calculan sobre los **53,000 MXN financiados**. El efectivo
elegido sigue siendo **50,000 MXN**: la llave no lo reduce a 47,000 ni se vuelve a sumar
por fuera del plan. Tampoco se realiza un desembolso en esta demo.

El desglose de capital, cuotas y total puede revisarse en el expediente del asesor. Si
la oferta o la tarjeta no coincide, no la apruebes para forzar el recorrido: revisa
los datos y la configuración. Si todo coincide, aprueba y espera la solicitud de documentos.

## 4. Adjuntar los documentos correctos

Usa **Upload PDF or Image** y envía cada archivo con su mensaje, esperando entre turnos:

| Archivo que debes adjuntar | Mensaje de Ana |
| --- | --- |
| [identity_ana.png](../fixtures/documents/identity_ana.png) | Adjunto mi identificación oficial. |
| [payslip_ana.pdf](../fixtures/documents/payslip_ana.pdf) | Adjunto mi recibo de nómina. |
| [ownership_ana.pdf](../fixtures/documents/ownership_ana.pdf) | Adjunto el comprobante de propiedad de mi auto. |

No escribas solo el nombre del archivo: adjúntalo realmente. El recibo de nómina de
10,000 MXN netos por quincena respalda los 20,000 MXN mensuales declarados.

## 5. Comprobar el resultado

- El expediente queda listo para revisión financiera después de las validaciones,
  sin promesas de aprobación, desembolso ni envío automático.
- La oferta elegida conserva el costo de llave de 3,000 MXN y el capital de 53,000 MXN.
- La reposición se incorporó **una sola vez**, y las cuotas corresponden a ese capital.

Revisa también la oferta y la cotización en la pantalla del asesor,
con el acceso indicado en el README. No basta con que el chat diga que incluyó la llave.

**Correspondencia técnica:** escenario HTTP `missing_second_key` en
[scripts/demo.py](../scripts/demo.py) y prueba de cotización e incorporación de llave
en [test_h3_credit.py](../tests/integration/test_h3_credit.py).
