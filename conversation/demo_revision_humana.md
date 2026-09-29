# Demo: revisión humana, del chat al asesor y de vuelta

**Objetivo:** provocar dos rondas documentales fallidas, resolver la lectura dudosa desde
la pantalla del asesor y consultar el resultado en el mismo chat. El asesor verifica
evidencia; el backend vuelve a validar antes de dejar el expediente listo para revisión
financiera. No se aprueba ni desembolsa un crédito.

Este es un guion con datos sintéticos. Envía una respuesta por turno y espera al agente;
las preguntas pueden cambiar de orden o redacción.

## Antes de empezar

- Sigue el [arranque y acceso del README](../README.md#arranque-rápido). Entra al chat
  como `cliente-ana` y abre **un hilo nuevo**; conserva ese hilo hasta el último paso.
- Usa `EXTRACTION_PROVIDER=fake`, el reloj de negocio de `.env.example` y los archivos
  originales del repositorio. El chat sí usa IA real y tiene costo; la extracción de
  esta demo es simulada. Con un extractor real el resultado puede variar y tener costo.
- Ten disponible el token de `asesor-1` para la segunda pantalla. Obtén los tokens
  localmente con las instrucciones del README; no los publiques ni pegues en el chat.

## 1. Iniciar y confirmar el auto

```text
Hola, quiero solicitar un crédito con garantía de mi auto.
```

| Dato solicitado | Respuesta de Ana: un mensaje |
| --- | --- |
| Titularidad | Sí, el auto está a mi nombre. |
| Adeudos o gravámenes | No, no tiene adeudos ni gravámenes que impidan usarlo como garantía. |
| Segunda llave | Sí, tengo la segunda llave. |
| Marca | Es un Nissan. |
| Modelo | Es un Versa. |
| Año | Es del año 2020. |
| Placa | La placa es ABC-123-XYZ. |

Revisa los siete datos y **aprueba la tarjeta del auto** mediante su botón.
La placa puede aparecer normalizada como `ABC123XYZ`.

## 2. Confirmar el perfil

| Dato solicitado | Respuesta de Ana: un mensaje |
| --- | --- |
| Nombre completo | Mi nombre completo es Ana Prueba López. |
| Domicilio | Mi domicilio es Calle Demo, número exterior 123, sin número interior, Colonia Ejemplo, municipio Ciudad de México, estado CDMX, código postal 00000, México. |
| Tipo de empleo | Soy asalariada. |
| Empresa | Trabajo en Empresa Sintética. |
| Ingreso neto | Mi ingreso neto es de 20,000 pesos mexicanos, después de impuestos. |
| Periodicidad | Ese importe corresponde a mi ingreso mensual. |

Si pide el domicilio por partes, responde solo el campo solicitado. **Aprueba la tarjeta
del perfil**, comprobando especialmente el ingreso neto mensual de 20,000 MXN.

## 3. Seleccionar una oferta

Espera las opciones. Si aparece la de 50,000 MXN de efectivo a 24 meses, envía:

```text
Elijo la opción de 50,000 pesos mexicanos de efectivo a 24 meses.
```

Revisa las cifras y **aprueba la tarjeta de selección**. No hay costo de reposición de
llave porque declaraste tenerla. Si la opción no aparece, revisa datos y configuración;
no pidas al agente que invente la oferta.

## 4. Completar documentos: primera ronda fallida

Usa **Upload PDF or Image**, adjunta el archivo y envía su mensaje. Hazlo uno por uno,
esperando la respuesta antes del siguiente. Escribir el nombre no adjunta el documento.

| Orden | Archivo | Mensaje de Ana |
| --- | --- | --- |
| 1 | [identity_ana.png](../fixtures/documents/identity_ana.png) | Adjunto mi identificación oficial. |
| 2 | [payslip_ana_low.pdf](../fixtures/documents/payslip_ana_low.pdf) | Adjunto mi recibo de nómina. |
| 3 | [ownership_ana.pdf](../fixtures/documents/ownership_ana.pdf) | Adjunto el comprobante de propiedad de mi auto. |

Al completar los tres, el sistema debe pedir corrección: la lectura de esta nómina
respalda 7,500 MXN netos por quincena, equivalentes a 15,000 mensuales, no los 20,000
declarados. Todavía no debe abrirse revisión por agotar las dos rondas.

Cuando pida aclarar el ingreso o reemplazar el comprobante, responde:

```text
Mi ingreso neto mensual correcto es de 20,000 MXN. Adjunté un comprobante equivocado y necesito reemplazarlo.
```

No cambies la declaración para ajustarla al archivo incorrecto.

## 5. Reemplazar la nómina: segunda ronda fallida

Adjunta [payslip_ana_lowconf.pdf](../fixtures/documents/payslip_ana_lowconf.pdf), con:

```text
Adjunto otro comprobante de ingresos para reemplazar el anterior.
```

Este archivo respalda 10,000 MXN netos por quincena, pero la lectura simulada del monto
tiene confianza 0.89, por debajo del mínimo de 0.90. El agente debe informar que la
solicitud necesita revisión de un asesor, sin presentarla como aprobada o resuelta.

**No subas todavía la nómina correcta ni pidas manualmente un asesor:** aquí se comprueba
la escalación automática por dos rondas fallidas. Repetir `payslip_ana_low.pdf` no cuenta
como nueva evidencia; debe ser el archivo distinto `payslip_ana_lowconf.pdf`.

## 6. Entrar como asesor y revisar el caso

1. Abre [la pantalla del asesor](http://127.0.0.1:8000/asesor) en otra pestaña. Ajusta
   el puerto si cambiaste la configuración del README.
2. Ingresa con el token de **`asesor-1`**, no con el de Ana.
3. En **Bandeja de revisiones**, abre el caso correspondiente. Confirma los datos de Ana
   y del auto; no elijas otro expediente solo porque también tenga una revisión abierta.
4. Comprueba el motivo **Dos rondas de corrección documental sin éxito** y el problema
   de lectura del monto en el comprobante de ingresos. Si ya tenías la pantalla abierta,
   recárgala para actualizar la bandeja.
5. En **Documentos**, descarga y abre el **recibo de nómina vigente**. Verifica visualmente
   los 10,000 MXN netos y su periodicidad quincenal.

## 7. Registrar la lectura humana

1. Abre **Registrar lectura** y selecciona **Recibo de nómina**.
2. Marca únicamente **monto del ingreso (confianza 0.89)**.
3. Conserva `10000.00` si lo verificaste en el original. **No pongas `20000`**: el campo
   corresponde al recibo quincenal; el backend realiza la comparación mensual.
4. En **Motivo de la lectura**, escribe:

   ```text
   Verifiqué en el documento original un ingreso neto de 10,000 MXN por quincena.
   ```

5. Pulsa **Registrar lectura humana**. No necesitas pulsar **Reanudar** después.

Si no puedes verificar el dato en el documento, no lo marques como leído: usa
**Solicitar corrección** para pedir evidencia legible. No ajustes cifras para forzar un OK.

El resultado esperado de este recorrido es **Expediente listo para revisión de la
financiera**, siempre que la oferta y demás evidencias sigan siendo válidas. El asesor
no asigna ese resultado: su lectura devuelve el caso a validación y el backend decide.

En **Eventos de auditoría**, comprueba esta secuencia técnica:

| Evento | Qué comprobar |
| --- | --- |
| `HUMAN_REVIEW_REQUESTED` | Dos rondas fallidas y revisión abierta. |
| `EXTRACTION_AMENDED` | Lectura humana del campo `income_amount`. |
| `HUMAN_REVIEW_RESOLVED` | Resolución `AMEND_EXTRACTION`, de vuelta a validación documental. |
| `VALIDATION_COMPLETED` | `all_pass: true` y lista de fallos vacía. |
| `CASE_READY` | Verificación final sin fallos. |

Estos códigos pertenecen a la auditoría del asesor, no a los mensajes del cliente.

## 8. Volver al mismo chat

La acción del asesor **no envía un mensaje automático al chat**. Vuelve al hilo original
y envía estas preguntas por separado, esperando cada respuesta:

```text
¿Ya terminó la revisión del asesor? ¿Cómo quedó mi solicitud?
```

```text
¿De cuánto sería el préstamo y a qué plazo?
```

```text
¿Podrías darme un resumen de cómo quedó mi proceso?
```

Debe consultar el expediente actualizado, informar que está listo para revisión financiera
y explicar la selección de 50,000 MXN a 24 meses con sus costos registrados. El monto máximo
del perfil no es el monto seleccionado. El cierre impide cambios, **no estas consultas**;
ninguna respuesta debe afirmar que el crédito está aprobado o desembolsado.

**Correspondencia técnica:** D15, cubierto por las pruebas de
[dos rondas fallidas](../tests/integration/test_h4_documents.py) y de
[resolución desde la pantalla del asesor](../tests/e2e_ui/test_advisor.py).
Las consultas posteriores al cierre tienen regresiones en
[test_graph.py](server/test_graph.py). Es una demostración de controles y participación
humana con extracción simulada, no una evaluación de calidad documental de IA real.
