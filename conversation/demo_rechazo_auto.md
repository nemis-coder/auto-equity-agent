# Demo: rechazo por elegibilidad del auto

**Objetivo:** comprobar que una declaración confirmada de falta de titularidad cierra
la solicitud antes de consultar Buró, presentar ofertas o pedir documentos.

Este es un guion con datos sintéticos, no una conversación ya ejecutada. Las preguntas
pueden variar; envía solo la respuesta que corresponda y espera al agente.

## Antes de empezar

Sigue el [arranque y acceso del README](../README.md#arranque-rápido), entra con el
token local de `cliente-ana` y abre **un hilo nuevo**. El chat usa un proveedor real
y tiene costo. Esta demo no necesita adjuntos.

## 1. Iniciar la solicitud

```text
Hola, quiero solicitar un crédito con garantía de mi auto.
```

## 2. Declarar que el auto no está a tu nombre

Cuando el agente pregunte si eres titular, responde:

```text
No, el auto no está a mi nombre.
```

Es suficiente para proponer una declaración que impide continuar. No completes por
anticipado marca, modelo, perfil ni ingreso para intentar avanzar al resto del proceso.

## 3. Confirmar la declaración

El agente debe mostrar una tarjeta. Comprueba que diga que **el auto no está a tu nombre**
y pulsa su opción de aprobar. Escribir «confirmo» en el chat no sustituye la tarjeta.

El modelo no debe rechazar por su cuenta: la confirmación permite que el backend aplique
la regla y registre el resultado.

## 4. Comprobar el resultado

- El agente explica que no puede continuar porque el auto no está a tu nombre.
- No presenta ofertas ni solicita documentos; no debe prometer aprobación o desembolso.
- La solicitud queda cerrada. Para probar otro caso, abre otro hilo; no cambies la
  titularidad en esta solicitud ya rechazada.

Puedes revisar el resultado y la auditoría del expediente en la pantalla del asesor,
con el acceso indicado en el README. La ausencia de consulta a Buró se comprueba en
esa auditoría, no solo por lo que diga el agente.

**Correspondencia técnica:** escenario HTTP `vehicle_not_owned` en
[scripts/demo.py](../scripts/demo.py) y pruebas de elegibilidad en
[test_h2_cases.py](../tests/integration/test_h2_cases.py).
