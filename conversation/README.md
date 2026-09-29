# El agente: conversación del cliente con Agent Chat UI + Aegra

La pantalla del cliente es un chat: [Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui) (LangChain, MIT) sobre [Aegra](https://github.com/aegra/aegra) (Apache 2.0), un servidor compatible con la API de LangGraph que no necesita la licencia del servidor oficial. El grafo `auto_equity` implementa **el agente** del sistema: pregunta, valida y pide aprobar cada dato en una tarjeta ([decisiones vigentes](../docs/auto_equity_TDD.md#14-decisiones-y-trade-offs-vigentes)). Guía para personas: [operación y demos](../docs/auto_equity_TDD.md#113-demo-progresiva).

Son tres servicios del `docker-compose.yml` principal: `agent-db` (PostgreSQL propio de Aegra), `agent` (Aegra con el grafo, puerto 2026) y `chat-ui` (puerto 3000). El agente llama a la API por la red interna (`http://api:8000`).

## Arquitectura

```
Agent Chat UI (Next.js, :3000) ──► Aegra (:2026) ──► grafo auto_equity (Anthropic u OpenAI)
                                                      │ herramientas y presupuesto = HTTP con el token del cliente
                                                      ▼
                                            API de Auto Equity (reglas, estado y presupuesto)
```

- **Autenticación:** el token del cliente se escribe en el campo «API Key» de la pantalla (viaja como `X-Api-Key`). `server/auth.py` lo valida con `GET /me` y solo admite clientes. Aegra aísla los hilos por usuario.
- **Herramientas del modelo (lista cerrada):** `consultar_solicitud`, `proponer_datos_del_auto`, `proponer_datos_personales`, `preparar_eleccion`, `subir_documento`, `reintentar` y `pedir_asesor`. Proponer y elegir no guardan nada; cualquier otra herramienta responde «Herramienta no permitida».
- **Pedir un asesor:** `pedir_asesor` llama a `POST /cases/{id}/review-requests`; el caso pasa a la bandeja del asesor. No es un rechazo.
- **Presupuesto por caso:** antes de cada llamada al modelo, el grafo pide `POST /cases/{id}/agent-turns`. La API cuenta el turno y reserva tokens del presupuesto persistente del caso. Al terminar, el grafo informa el uso real (`…/usage`), que la API liquida y audita como `agent.usage`, sin texto. Sin presupuesto, el modelo no se llama y un asesor toma el caso.
- **Observabilidad:** `agent.turn` mide la llamada real dentro del grafo. Herramientas, HTTP y extracción comparten contexto; costos estimados con tarifas explícitas y JSONL local además de Langfuse opcional. [Guía y límites](../docs/auto_equity_TDD.md#10-observabilidad).
- **Contexto acotado:** el modelo ve unos 12k tokens de historial, cortados antes de un mensaje de la persona; el estado vigente lo trae `consultar_solicitud`.
- **Documentos:** la persona los adjunta con «Upload PDF or Image». El modelo solo indica el tipo; la API los valida y los lee. **Los adjuntos no se reenvían al modelo del chat**: se reemplazan por «[Adjunto: nombre]», por costo y privacidad.
- **Confirmación:** es una interrupción (`interrupt()`) con el formato HITL de LangChain. Agent Chat UI la muestra como tarjeta con aprobar o rechazar. Solo la decisión de la persona hace que el **código** llame a `/confirmations` o `/selections`; el modelo nunca confirma ni elige.
- **Elegibilidad:** la decide el sistema. Con una respuesta que impide continuar, el modelo propone esa respuesta y la API registra el rechazo.
- **Validación antes de la tarjeta:** la API valida las declaraciones con su contrato de dominio; el grafo solo adapta los campos y presenta el resultado. Un 422 vuelve al modelo como `datos_invalidos`, sin tarjeta. Las propuestas parciales conservan el contrato de la API: los campos faltantes se solicitan antes de avanzar. La elección se busca en las ofertas vigentes y la API la verifica al confirmar.
- **Adjuntos fuera del estado:** tras subirlo a la API, el archivo se reemplaza en el mensaje por «[Adjunto subido: nombre]». Los hilos se borran solos a los 7 días (`AEGRA_THREAD_TTL`), con sus checkpoints.

## Proveedor de IA (obligatorio para conversar)

El agente usa el proveedor de `CHAT_PROVIDER` y su clave, definidos en `.env`:

| `CHAT_PROVIDER` | Clave | Modelo por defecto (`CHAT_MODEL` vacío) |
| --- | --- | --- |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` |
| `openai` | `OPENAI_API_KEY` | Sin predeterminado: `CHAT_MODEL` explícito, con Responses y herramientas |

Sin proveedor real, clave o modelo obligatorio, el servicio arranca igual (la API y la pantalla
del asesor no lo necesitan), el log lo advierte y el chat responde qué variable falta.
Primera instalación: `docker compose up -d --build agent`; cambios posteriores de `.env`:
`docker compose up -d --force-recreate agent`. Cada llamada conserva el presupuesto del caso.

No hay fallback automático. El grafo conserva herramientas y confirmaciones con ambos
proveedores. Al cambiar proveedor/modelo abre un hilo nuevo: los hilos creados desde 0043
detectan el cambio y no envían su historial al nuevo modelo. Los hilos anteriores deben
cerrarse antes de cambiar. Los documentos se configuran aparte, con `EXTRACTION_PROVIDER`.
[Configuración](../docs/auto_equity_TDD.md#112-proveedores-de-ia) ·
[Trade-off para la entrevista](../docs/entrega.md#decisiones-razonamiento-y-trade-offs).

## Uso

Con el stack arriba (`docker compose up -d --build`), abre http://localhost:3000 y en el formulario escribe:
- **Deployment URL:** `http://localhost:2026`
- **Assistant / Graph ID:** `auto_equity`
- **LangSmith API Key:** el token de `cliente-ana` (u otro cliente)

El navegador habla directo con Aegra, que solo acepta llamadas desde `http://localhost:3000` (CORS en `server/aegra.json`). Si cambias `CHAT_UI_PORT`, agrega ese origen ahí.

## Experiencia de cliente (0040)

El agente pide los datos faltantes uno por uno, explica el avance sin códigos internos y no promete contacto ni aprobación. Las ofertas se presentan en tabla con todos sus costos. La interfaz oculta las herramientas por defecto y muestra el progreso de cada documento; las tarjetas de aprobación siguen visibles. El filtro de estados solo afecta a la presentación y al texto copiado, no al registro original. [Decisión y límites](../docs/auto_equity_TDD.md#52-contexto-conversación-y-confirmaciones).

## Pruebas

- **Pruebas del grafo**, sin red ni modelo: configuración de ambos proveedores, validación,
  rutas, adjuntos, presupuesto, contexto, elección, asesor y cambio de modelo entre hilos.
- **Contratos HTTP de ambos SDK**, en `tests/unit/test_chat_transport.py`: herramientas,
  historial, streaming, rechazos, errores y consumo. No miden la calidad de un modelo real.
- **Pruebas de presentación**, sin navegador ni modelo: `node --test conversation/chat-ui/customer-view.test.mjs`. Cubren estados completos/parciales y progreso, incluidos errores e interrupciones.
- **De punta a punta contra la API:** `tests/integration/test_chat_agent.py` y los escenarios D04, D05, D22 y D25 de la evaluación. Ejecutan este mismo grafo con un modelo guionado (`evals/chat_agent.py`).

```bash
docker compose exec agent python -m unittest -v test_graph
```

## Resultados de la prueba (2026-09-25, macOS arm64)

| Verificación | Resultado |
| --- | --- |
| Sin token, token inválido o token de asesor | 401 |
| Beto lee o lista el hilo de Ana | 404, no lo ve |
| Un mensaje con todos los datos | Claude: `consultar_solicitud` → `proponer_datos_del_auto` → pausa en la tarjeta |
| Aprobar | `/confirmations` con `If-Match` e idempotencia; el caso pasa a `PROFILING` |
| Recorrido completo (auto, perfil, oferta, 3 documentos) | Tarjetas en cada paso y ofertas con cifras exactas; los documentos los lee la API |
| Corrección de domicilio | Claude pidió el dato, re-propuso el perfil (tarjeta), la solicitud volvió a Opciones, se eligió de nuevo y llegó a `READY_FOR_FINANCIAL` |
| Adeudo declarado | Tarjeta con las dos respuestas; la API registra `CASE_REJECTED` |
| Datos inválidos (año 1850, CP de 3 dígitos, calle con número) | Sin tarjeta; el modelo recibe los problemas y vuelve a preguntar |
| CORS desde `localhost:3000` | Permitido, incluida la cabecera `x-api-key` |

## Hallazgos

- **Documentación de Aegra inexacta:** dice que `langgraph_auth_user` es un dict; en 0.10.5 es un modelo `User` con los campos extra como atributos.
- **Agent Chat UI oculta el formulario inicial** si se definen `NEXT_PUBLIC_API_URL` y `NEXT_PUBLIC_ASSISTANT_ID`, y entonces no hay dónde escribir el token. Por eso no se preconfiguran.
- **Agent Chat UI solo dibuja la tarjeta si el último mensaje es del asistente.** La pausa ocurre justo después del mensaje con la llamada a la herramienta y antes de su resultado. Además se pide una herramienta por turno (`parallel_tool_calls=False`). Si la persona escribe con una tarjeta pendiente, su mensaje se retira y la tarjeta vuelve a mostrarse, sin llamar al modelo.
- **Errores de captura del modelo:** recortó «Colonia Ejemplo» a «Ejemplo» y duplicó el número («Calle Demo 123 123»). Se corrigieron con reglas en el prompt y en la descripción de la herramienta. **La tarjeta es la defensa:** muestra exactamente lo que se guardará.
- **Preguntas repetidas y rechazo por su cuenta:** el modelo volvía a preguntar un «sí» claro y rechazó una solicitud sin registrarla. Se corrigió en el prompt y haciendo opcionales los datos del auto cuando una respuesta ya impide continuar.
- **Token con otro valor:** si el campo «API Key» guarda comillas, espacios o el texto de ejemplo, todo da 401. `auth.py` tolera comillas y espacios y registra solo la longitud y el prefijo del token rechazado.
- **Afirmaciones falsas del modelo:** dijo «tarjeta pendiente» cuando ya estaba aprobada y «corrección resuelta» cuando el caso pasó a asesor. Se corrigieron explicando en el prompt cómo leer los resultados y cada etapa. Falta medirlo con más casos.
- **Hilo bifurcado tras subir documentos (corregido):** Aegra devuelve 10 puntos del historial por defecto y subir tres documentos genera 11. Agent Chat UI calculaba mal la punta del hilo y el siguiente mensaje salía desde antes de la subida: esa rama «no veía» los documentos y el resultado huérfano que agrega la pantalla hacía fallar a Claude (400). Se corrigió pidiendo hasta 1000 puntos (`chat-ui/Dockerfile`) y omitiendo resultados huérfanos al armar la vista del modelo (`_for_model`).
- **Montos como fórmula (corregido):** con dos `$` en una línea, remark-math los dibujaba como LaTeX; se desactivó la matemática con un solo `$`.
- **Solicitud cerrada:** no admite cambios, pero sí consultas. En «Listo», el servidor presenta monto, plazo, cuotas y costos de la oferta seleccionada o un resumen del proceso según la pregunta; conserva el aviso de no aprobación. No sustituye todas las respuestas por el mismo cierre ni toma cifras de otra oferta. Si una cotización venció, la identifica como histórica. No hay aviso automático tras la acción del asesor: el cliente vuelve a preguntar en el mismo hilo.
- **Corrección de ingresos:** la consulta incluye el ingreso declarado y su periodicidad. Un fallo de comparación no exige que un recibo quincenal muestre el importe mensual ni cambia automáticamente lo declarado. La explicación controlada de ese fallo permite aclarar la declaración o reemplazar el documento; las conversiones y validaciones siguen en la API.
- **Validación inconsistente del código postal (corregido):** el modelo a veces aceptaba «00000» y a veces lo cuestionaba por su conocimiento de México, aunque el código lo acepta (y los documentos de prueba lo usan). El prompt ahora dice que la validez la decide la API (0039) y que no juzgue si un dato existe; una prueba fija que prompt y código coinciden. Verificado con Claude: 3 de 3 pasan directo a la tarjeta.
- **Sin licencia:** Aegra funciona sin claves de LangSmith y sin Redis (modo de un contenedor, sin recuperación ante caídas).

## Limitaciones conocidas

- **El token del cliente viaja en la configuración de cada corrida** (`langgraph_auth_user.api_token`) y Aegra podría persistirla en su base. En producción habría que usar un token de servicio delegado o de corta vida.
- La pantalla guarda el token en `localStorage` del navegador.
- Las tarjetas son las genéricas de Agent Chat UI (clave: valor). Unas tarjetas de ofertas más visuales necesitarían UI generativa (React).
- Los adjuntos ya no quedan en el estado actual, pero sí en el historial de checkpoints hasta que vence la retención (7 días).
- La evaluación ejecuta este grafo con un modelo guionado: mide orquestación y controles, no la calidad de un modelo real. Falta una pista con modelo real para la conversación, como la que ya existe para la lectura de documentos.
- El modelo recibe los datos personales que la persona escribe (necesario para capturarlos conversando); en producción, proveedor con acuerdo de no retención.
- Aegra es un proyecto comunitario (~1.2k estrellas); falta evaluar su madurez para producción.
