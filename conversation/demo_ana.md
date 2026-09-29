# Demo de Ana: camino feliz paso a paso

Guion con datos sintéticos para interactuar con el agente desde el inicio hasta dejar
el expediente listo para revisión financiera. **No es una transcripción de una ejecución:**
las preguntas son orientativas y el agente puede cambiar su redacción u orden.

**Envía una respuesta a la vez y espera al agente.** No pegues el guion completo ni
las preguntas de ejemplo. Si un dato ya quedó respondido, no hace falta repetirlo.

## Antes de empezar

- Sigue el [arranque y acceso del README](../README.md#arranque-rápido) y entra con el
  token local de `cliente-ana`. La clave del proveedor de IA va en `.env`, no en el chat.
- Abre un hilo nuevo para este recorrido. No mezcles esta demo con una solicitud ya cerrada.
- Para practicar con resultados documentales simulados, conserva `EXTRACTION_PROVIDER=fake`
  y el reloj de negocio de `.env.example`. El chat sí requiere un proveedor real y tiene costo.
- Usa los archivos originales de `fixtures/documents/`: el extractor `fake` los reconoce
  por su contenido, no por el nombre. No los edites ni uses documentos personales reales.
- Si configuras extracción real, su resultado puede variar y tiene costo. Este guion no
  sustituye la [evaluación de calidad documental](../docs/auto_equity_TDD.md#84-evidencia-y-limitaciones).

## 1. Iniciar la solicitud

Envía este mensaje:

```text
Hola, quiero solicitar un crédito con garantía de mi auto.
```

Espera la primera pregunta. No envíes todavía todos los datos del auto.

## 2. Responder sobre el auto

Copia solo la respuesta de Ana que corresponda a lo que pregunte el agente:

| Pregunta orientativa del agente | Respuesta de Ana: un mensaje |
| --- | --- |
| ¿El auto está a tu nombre? | Sí, el auto está a mi nombre. |
| ¿Tiene adeudos o gravámenes que impidan usarlo como garantía? | No, no tiene adeudos ni gravámenes que impidan usarlo como garantía. |
| ¿Tienes la segunda llave? | Sí, tengo la segunda llave. |
| ¿De qué marca es? | Es un Nissan. |
| ¿Cuál es el modelo? | Es un Versa. |
| ¿De qué año es? | Es del año 2020. |
| ¿Cuál es la placa o referencia del vehículo? | La placa es ABC-123-XYZ. |

**Primera tarjeta: datos del auto.** Comprueba titularidad **sí**, adeudo bloqueante **no**,
segunda llave **sí**, Nissan Versa 2020 y placa `ABC-123-XYZ`. La referencia puede mostrarse
normalizada como `ABC123XYZ`.

Pulsa la opción de aprobar de la tarjeta si todo coincide. Si hay un error, recházala y
explica qué corregir. **Escribir «sí» o «confirmo» en el chat no sustituye la aprobación
de la tarjeta.** Espera a que el agente continúe.

## 3. Responder los datos personales y de ingreso

| Pregunta orientativa del agente | Respuesta de Ana: un mensaje |
| --- | --- |
| ¿Cuál es tu nombre completo? | Mi nombre completo es Ana Prueba López. |
| ¿Cuál es tu domicilio completo? | Mi domicilio es Calle Demo, número exterior 123, sin número interior, Colonia Ejemplo, municipio Ciudad de México, estado CDMX, código postal 00000, México. |
| ¿Eres asalariada o independiente? | Soy asalariada. |
| ¿En qué empresa trabajas? | Trabajo en Empresa Sintética. |
| ¿Cuál es tu ingreso neto, después de impuestos? | Mi ingreso neto es de 20,000 pesos mexicanos. |
| ¿Ese ingreso corresponde a qué período? | Ese importe corresponde a mi ingreso mensual. |

Si pregunta el domicilio por partes, responde solo el campo solicitado:

| Campo solicitado | Respuesta de Ana |
| --- | --- |
| Calle | La calle es Calle Demo. |
| Número exterior | El número exterior es 123. |
| Número interior | No tengo número interior. |
| Colonia | La colonia es Colonia Ejemplo. |
| Municipio o alcaldía | El municipio es Ciudad de México. |
| Estado | El estado es CDMX. |
| Código postal | El código postal es 00000. |
| País | México. |

Si pregunta si los 20,000 pesos son antes o después de impuestos, responde:

```text
Son 20,000 pesos mexicanos netos al mes, después de impuestos.
```

**Segunda tarjeta: perfil.** Revisa el nombre, domicilio, empleo, empresa e ingreso
**neto mensual de 20,000 MXN**. La calle debe ser `Calle Demo` y el número exterior `123`
en campos separados; conserva `Colonia Ejemplo` y `00000` exactamente. Aprueba si coincide.

Nota para quien realiza la demo: el comprobante de nómina incluido muestra **10,000 MXN
netos por quincena**, equivalentes a los **20,000 MXN mensuales** declarados. No cambies el
ingreso mensual a 10,000 por leer una sola quincena.

## 4. Elegir y confirmar una oferta

Espera a que el agente muestre las opciones. Para este recorrido, si aparece una oferta
de **50,000 MXN de efectivo a 24 meses**, envía:

```text
Elijo la opción de 50,000 pesos mexicanos de efectivo a 24 meses.
```

**Tercera tarjeta: oferta elegida.** Compara el efectivo, plazo, cuota, último pago,
costo de llave y total con la oferta mostrada. En este camino declaraste tener segunda
llave, así que no debe cobrarse su reposición. Aprueba solo si las cifras coinciden.

No copies tasas ni cuotas de otra ejecución: usa las cifras de la oferta vigente.
Si esa combinación no aparece, no pidas inventarla; revisa los datos confirmados y elige
una de las opciones realmente disponibles o solicita ayuda al asesor.

## 5. Adjuntar los documentos, uno por uno

Cuando el agente los solicite, usa **Upload PDF or Image** (adjuntar archivo).
En cada turno selecciona el archivo, acompáñalo con el mensaje indicado y envíalo.
**Escribir el nombre del archivo no lo sube.** Espera la respuesta antes del siguiente adjunto.

### Identificación

Adjunta [identity_ana.png](../fixtures/documents/identity_ana.png) y envía:

```text
Adjunto mi identificación oficial.
```

### Comprobante de ingresos

Adjunta [payslip_ana.pdf](../fixtures/documents/payslip_ana.pdf) y envía:

```text
Adjunto mi recibo de nómina.
```

### Propiedad del auto

Adjunta [ownership_ana.pdf](../fixtures/documents/ownership_ana.pdf) y envía:

```text
Adjunto el comprobante de propiedad de mi auto.
```

El orden puede variar si el agente pide primero otro documento. La validación del conjunto
ocurre después de completar los documentos requeridos: que un archivo haya sido recibido
no significa que el expediente completo ya pasó todas las reglas.

## 6. Comprobar el cierre

Con los datos y documentos correctos, el resultado esperado es un expediente **listo para
revisión financiera**. El agente debe explicarlo en español, sin códigos internos y sin
prometer aprobación, desembolso, contacto ni envío automático a una financiera.

Si necesitas consultar el avance, envía:

```text
¿Mi expediente ya está listo o falta algo por completar?
```

Si aparece una corrección o revisión humana, sigue esa indicación: no asumas que el
recorrido terminó bien solo por haber cargado los tres archivos. Una solicitud ya cerrada
no admite reemplazos; para repetir la demo, abre una conversación nueva.

## Si el recorrido se detiene

- **Tarjeta pendiente:** usa sus controles antes de enviar más mensajes. Rechaza si necesitas corregir.
- **Error de servicio:** cuando se haya resuelto la causa, puedes escribir «Quiero reintentar el paso pendiente»; no supone que el error desaparezca por sí solo.
- **Archivo no reconocido con `fake`:** selecciona el archivo original del repositorio, no una copia editada o una captura.
- **Falta de configuración de IA:** revisa las variables indicadas y los pasos del README; no pegues claves en el chat.

Los datos se corresponden con [scripts/demo.py](../scripts/demo.py) y los archivos de
prueba de [fixtures/documents/](../fixtures/documents/). Las demos HTTP complementan este
recorrido; no reemplazan la interacción real con el agente ni certifican calidad de extracción.
