# Demo: validación documental fallida

**Objetivo:** comprobar que un ingreso declarado sin respaldo suficiente en la nómina
provoca una solicitud de corrección, no un expediente listo para revisión financiera.

Este es un guion con datos sintéticos, no una conversación ya ejecutada. Envía una
respuesta por turno; las preguntas del agente pueden cambiar de orden o redacción.

## Antes de empezar

- Sigue el [arranque y acceso del README](../README.md#arranque-rápido), entra como
  `cliente-ana` y abre **un hilo nuevo**, distinto del camino feliz.
- Para reproducir este resultado, usa `EXTRACTION_PROVIDER=fake`, el reloj de negocio
  de `.env.example` y los archivos originales del repositorio. El chat sí usa IA real
  y tiene costo; con extracción real también hay costo y el resultado puede variar.
- No subas la nómina correcta durante esta demo: queremos observar el bloqueo por
  discrepancia, no resolverlo antes de comprobarlo.

## 1. Iniciar y confirmar los datos del auto

```text
Hola, quiero solicitar un crédito con garantía de mi auto.
```

Responde solo cuando el agente pregunte por cada dato:

| Dato solicitado | Respuesta de Ana: un mensaje |
| --- | --- |
| Titularidad | Sí, el auto está a mi nombre. |
| Adeudos o gravámenes | No, no tiene adeudos ni gravámenes que impidan usarlo como garantía. |
| Segunda llave | Sí, tengo la segunda llave. |
| Marca | Es un Nissan. |
| Modelo | Es un Versa. |
| Año | Es del año 2020. |
| Placa | La placa es ABC-123-XYZ. |

Revisa esos siete datos y **aprueba la tarjeta del auto**. La placa puede mostrarse
normalizada como `ABC123XYZ`.

## 2. Declarar y confirmar el perfil

| Dato solicitado | Respuesta de Ana: un mensaje |
| --- | --- |
| Nombre completo | Mi nombre completo es Ana Prueba López. |
| Domicilio | Mi domicilio es Calle Demo, número exterior 123, sin número interior, Colonia Ejemplo, municipio Ciudad de México, estado CDMX, código postal 00000, México. |
| Tipo de empleo | Soy asalariada. |
| Empresa | Trabajo en Empresa Sintética. |
| Ingreso neto | Mi ingreso neto es de 20,000 pesos mexicanos, después de impuestos. |
| Periodicidad | Ese importe corresponde a mi ingreso mensual. |

Si pregunta el domicilio por partes, usa las
[respuestas por campo del guion de Ana](demo_ana.md#3-responder-los-datos-personales-y-de-ingreso).
**Aprueba la tarjeta del perfil** después de comprobar el ingreso neto mensual de
20,000 MXN y el resto de los datos. No cambies el ingreso para hacerlo coincidir con
el documento que subirás después.

## 3. Elegir una oferta

Espera las opciones. Si aparece la de 50,000 MXN de efectivo a 24 meses, envía:

```text
Elijo la opción de 50,000 pesos mexicanos de efectivo a 24 meses.
```

Compara las cifras con la oferta vigente y **aprueba la tarjeta de selección**. No debe
haber costo de reposición porque declaraste tener segunda llave. Si la opción no aparece,
revisa los datos y la configuración; no pidas inventarla.

## 4. Subir los documentos, uno por uno

Usa **Upload PDF or Image**, selecciona el archivo y envía su mensaje. Espera la
respuesta antes de subir el siguiente; escribir el nombre no adjunta el archivo.

| Archivo que debes adjuntar | Mensaje de Ana |
| --- | --- |
| [identity_ana.png](../fixtures/documents/identity_ana.png) | Adjunto mi identificación oficial. |
| [payslip_ana_low.pdf](../fixtures/documents/payslip_ana_low.pdf) | Adjunto mi recibo de nómina. |
| [ownership_ana.pdf](../fixtures/documents/ownership_ana.pdf) | Adjunto el comprobante de propiedad de mi auto. |

**La diferencia frente al camino feliz es `payslip_ana_low.pdf`.** Su lectura simulada
contiene 7,500 MXN netos por quincena, equivalentes a 15,000 MXN mensuales, frente a
los 20,000 declarados. La discrepancia supera la tolerancia del 10 % de esta política.

Completa los tres documentos: la validación del conjunto ocurre al tener todas las
evidencias requeridas. Recibir un archivo no equivale a aprobarlo.

## 5. Comprobar la solicitud de corrección

El agente debe explicar que el comprobante de ingresos no coincide con lo declarado
y pedir aclaración o reemplazo. Si pregunta cuál es el ingreso correcto, responde:

```text
Mi ingreso neto mensual correcto es de 20,000 pesos mexicanos. Necesito reemplazar el comprobante que adjunté.
```

**Detén aquí esta demo**, sin adjuntar todavía el reemplazo. Debes observar:

- Corrección documental pendiente, identificando el comprobante de ingresos.
- El expediente no está listo para revisión financiera y no se presenta como aprobado.
- Esta primera ronda fallida pide corrección; no es por sí sola un rechazo de elegibilidad
  ni el escalamiento automático por agotar dos rondas.

Comprueba la discrepancia y el estado del expediente en la pantalla del asesor, con el
acceso indicado en el README. No te bases únicamente en la respuesta del chat.

**Correspondencia técnica:** escenario HTTP `document_income_mismatch` en
[scripts/demo.py](../scripts/demo.py) y pruebas de discrepancia de ingresos en
[test_h4_documents.py](../tests/integration/test_h4_documents.py).
Este recorrido comprueba reglas con extracción simulada, no calidad documental de IA real.
