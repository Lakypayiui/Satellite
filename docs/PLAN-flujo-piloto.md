# Plan: flujo decisivo con un agente piloto

Origen: auditoría del 2026-10-22. Satellite decide qué hacer con cada mensaje mediante
una cascada de expresiones regulares (`orchestrator.py`, puertas de las líneas 262-325)
y un planner JSON de un turno sin tools. El usuario no ve streaming, las preguntas
pendientes se pierden y la respuesta final es la salida del último nodo.

Objetivo: que cada turno lo conduzca **un único agente piloto con tools**, como en
Claude Code u opencode, y que los microagentes especializados trabajen **por debajo**
de él. Se conservan agentes, tools, seguridad, sandbox, aprobaciones y las tres UIs.

## Estado (2026-10-22, rama `feat/pilot-flow`)

| Fase | Estado |
|---|---|
| 0 Red de seguridad | Hecha. Bandera `orchestration.mode`, contrato del turno en `tests/test_turn_contract.py`. |
| 1 Streaming | Hecha. `stream()` en OpenAI-compat, Anthropic y ChatGPT; envoltorios de medición y Safe Proxy; `TEXT_DELTA` en el bus. |
| 2 Piloto | Hecha. `satellite/orchestration/pilot.py`, spec y prompts `pilot`, D1 aplicada en `DelegationManager`. |
| 3 Preguntas abiertas | Hecha. Tool `ask_user`, `Turn.question`, `answering` en el turno siguiente, modo plan con el piloto. |
| 4 TUI | Hecha salvo el `TextArea` opcional. |
| 5 Escritorio | Hecha. Cerrojo 409, `/status.turn`, eventos de aprobación, reconexión SSE, burbuja en vivo, preguntas con botones. |
| 6 Retiro del legado | Hecha. 6a: `pilot` por defecto. 6b: borrados la cascada de `DefaultOrchestrator`, el planner, el graph runner, los flujos por regex y la bandera; `describe_runtime` sustituye a `catalog_answer`. Pendiente menor: el modal de alternativas (`plan_options`) de TUI/escritorio/CLI ya no se alcanza. |
| 7 Evaluación | Hecha con `commandcode / deepseek-v4.1-flash`; ver abajo. |

Evaluación con `commandcode / deepseek-v4.1-flash` (2026-10-22). Tras añadir presupuesto
de exploración (12 rondas, 6 resultados enteros), lecturas por rango y la protección de
la respuesta final, las preguntas de código bajaron de 0,5-2,6 M a 0,09-0,25 M tokens de
entrada y el primer texto aparece en ~2 s. El coste restante está en los agentes
delegados (`python_coder`, `python_auditor`), que aún no tienen presupuesto.

Segunda ronda: los agentes delegados reciben presupuesto (`DELEGATE_TOOL_ROUND_BUDGET` = 20,
`DELEGATE_KEEP_TOOL_RESULTS` = 8) y el piloto ya no encadena auditor tras un coder cuya
verificación pasó. En los tres casos que cambian código el consumo bajó de 11,9 M a 1,6 M
tokens de entrada (253 → 112 llamadas, ~38 → ~19 min) y los tres casos cumplen sus checks.

Tercera ronda (`pilot` por defecto, delegados con 25 rondas y aviso de trabajo
incompleto, reintentos del proveedor): los 10 casos cumplen sus checks; 198 llamadas y
3,4 M tokens de entrada en total (antes 326 y 12,9 M). Lo que más pesa sigue siendo
`python_coder` en «hazlo»: agota las 25 rondas dos veces seguidas (1,3 M tokens).

Cuarta ronda, tras la traza de tools de los delegados (`scripts/eval_flow.py` la imprime):
`code_task action=test` respeta `path` (antes lanzaba la suite entera en cada
verificación), `search_files` busca texto con `content` (antes los coders recurrían a
`grep`/`rg` vía `run_command`), los prompts de coders y auditores tienen pautas de
trabajo económico, `delegate` rechaza un coder de otro lenguaje y el presupuesto vuelve a
20 rondas. Resultado: 10/10 casos, 192 llamadas, 2,5 M tokens de entrada, ~13,5 min.
Pendiente: los delegados releen rangos ya leídos (se recortan los resultados viejos)
y en «hazlo» agotan las 20 rondas.

Decisiones aplicadas: D1 y D2 como se proponían. D3: el runtime no registra qué modelos
soportan tools, así que no hay detección automática; `doctor` muestra el modo activo y
`pilot` es el valor por defecto desde la fase 6a. Fase 6b: D3 se resuelve exigiendo un
modelo con tools para el rol `orchestrator` (no queda ruta sin tools; `doctor` lo
recuerda y comprueba que el agente `pilot` esté cargado). D4: MCP ejecuta el mismo piloto
con `orchestrator.execute`; la elicitación para `ask_user` queda pendiente (hoy la
pregunta llega como texto).

Criterios de éxito:

- Ninguna decisión de ruta depende de una regex.
- La primera palabra de la respuesta aparece en pantalla mientras el modelo escribe.
- Una aclaración y su respuesta («el segundo») se enlazan siempre.
- La respuesta visible es una síntesis del piloto, no el JSON del último nodo.
- Los mensajes de la tabla de la auditoría toman la ruta correcta (ver fase 7).

---

## Fase 0 · Red de seguridad y bandera

1. Añadir `orchestration.mode` en `.satellite/config/config.json` con valores
   `pilot` y `legacy`. `legacy` es el valor por defecto hasta la fase 6.
2. `build_runtime` (`satellite/cli/runtime_builder.py:114`) construye un orquestador u
   otro según la bandera. Ambos implementan `Orchestrator.execute(task)`, así que
   `ChatSession`, la TUI, la CLI, el backend de escritorio y MCP no cambian de firma.
3. Tests de conversación con `FakeProvider` que fijen el comportamiento observable del
   turno: texto final, estado, eventos emitidos y pregunta pendiente. Se escriben
   contra la interfaz de `ChatSession.send`, no contra el orquestador interno, para
   que sirvan a los dos modos.

Entregable: la suite actual sigue verde y existe un esqueleto `PilotOrchestrator` que
de momento delega en el legado.

## Fase 1 · Streaming en el contrato de proveedor

1. `LLMProvider` (`satellite/providers/base.py`) gana un método no abstracto
   `stream(request) -> AsyncIterator[StreamChunk]`. La implementación por defecto llama
   a `complete()` y emite un único chunk, así ningún proveedor existente se rompe.
2. `StreamChunk` en `satellite/core/messages.py`: `text_delta`, `tool_call_delta`,
   y un chunk final con el `LLMResponse` completo (uso, stop_reason, tool_calls).
3. Implementar streaming real en `openai_compat.py`, `anthropic_provider.py` y
   `chatgpt_plan.py`. Este último ya recibe `output_text.delta` en las líneas 58-120;
   basta con ceder cada delta en vez de acumularlo.
4. `metered.py` y la capa de caché envuelven `stream()` igual que `complete()`, para
   no perder métricas ni diagnósticos de prefijo.
5. Nuevo `EventType.TEXT_DELTA` en `satellite/core/events.py`, con `task_id`, `node_id`
   y el texto. El bus agrupa deltas cada 50 ms para no saturar SSE ni la TUI.
6. No se persisten los deltas en `.satellite/executions`; solo el texto final.

Entregable: una llamada en streaming produce eventos `TEXT_DELTA` en el bus.

## Fase 2 · El agente piloto

1. Spec `satellite/agents/specs/pilot.json` y prompts `pilot.system.txt` /
   `pilot.user.txt`. `model_role: orchestrator`, `max_turns` alto (por ejemplo 30).
2. Tools del piloto:
   - Lectura: `read_file`, `list_dir`, `search_files`, `lsp`, `git_status`,
     `request_context`.
   - Acción: `delegate` hacia cualquier agente del catálogo.
   - Conversación: `ask_user` (nueva, fase 3).
   - Sin `write_file`, `edit_file` ni `run_command`. Escribir y ejecutar es trabajo de
     los `<lenguaje>_coder`. Ver la decisión D2.
3. El catálogo de agentes viaja en el bloque estable del system prompt usando
   `build_catalog_context` (`planner_prompt.py:29`). Ya es determinista y cacheable.
4. `can_call` del piloto se calcula en tiempo de construcción con todo el catálogo.
   Los agentes que cree `agent_creator` se añaden al reconstruir el runtime.
5. Escalado de permisos. `DelegationManager._requires_escalation`
   (`delegation.py:150`) pediría aprobación cada vez que el piloto, sin escritura,
   delega en un coder. El piloto se marca como **raíz de sesión**: su techo de
   permisos es el de la política de la sesión, no el de su lista de tools. Las
   escrituras siguen pasando por zonas de escritura, sandbox y modo `ask/auto/unrestricted`.
6. El piloto sustituye a los flujos deterministas. Diagramas, escritorio y web pasan a
   ser delegaciones normales hacia `pele_diagram_agent`, `desktop_operator` y
   `web_researcher`. `desktop_operator` conserva su aprobación humana obligatoria
   porque vive en las tools, no en la ruta.
7. El piloto usa `stream()` en todas sus llamadas. Los subagentes delegados pueden
   seguir con `complete()`; su progreso se ve como eventos de tool, no como texto.
8. La respuesta del turno es el último mensaje de texto del piloto. Los resultados de
   los agentes delegados le llegan como resultado de la tool `delegate`, y él los
   resume. Esto elimina `_answer_in_prose` y `readable_output` como fuente de la
   respuesta visible.
9. `PilotOrchestrator.execute` queda en: adjuntar índice e idiomas como hoy, crear el
   piloto, ejecutarlo y traducir su resultado a `TaskResult`. Sin cascada.

Reglas clave del prompt del piloto:

- Responder directamente si la pregunta no requiere el proyecto.
- Leer antes de afirmar algo sobre el código.
- Delegar cuando hay que cambiar archivos, ejecutar tests o usar una capacidad que
  solo tiene otro agente. Una instrucción de delegación es autocontenida.
- Preguntar con `ask_user` solo cuando la ambigüedad cambia el trabajo.
- Si falta una capacidad, delegar en `agent_creator`. Se conserva la promesa de
  «nunca un callejón sin salida» del README.

Entregable: con `mode: pilot`, una pregunta sobre el código lee archivos y una
petición de cambio delega en el coder del lenguaje del proyecto.

## Fase 3 · Estado conversacional: preguntas abiertas

1. Tool `ask_user(question, choices?: [{id, label, detail}], recommended?)`. Termina el
   turno con `TaskStatus.NEEDS_USER_INPUT` y `output.mode = "question"`.
2. `ChatSession` (`satellite/orchestration/chat.py`) sustituye `_pending_plan` por
   `_pending_question`, que cubre tanto aclaraciones como alternativas de plan.
   Se persiste con la sesión para sobrevivir a una recarga.
3. En el turno siguiente, `send` añade a la tarea `answering: {question, choices}`.
   El piloto lo recibe como contexto explícito, así que «el segundo» o «sí» se
   resuelven sin heurística. Se elimina `_accepts_recommendation` y la detección de
   «hazlo» en `chat_detector.py` y en `planner.py:131`.
4. `/plan <tarea>` se convierte en una instrucción al piloto: investigar en solo
   lectura, proponer de dos a cuatro enfoques con `ask_user` y no delegar escrituras.
   `PlanOptionGenerator` y `plan_options.*.txt` se retiran en la fase 6.
5. Elegir una opción desde la UI envía el `id` de la opción como respuesta normal a
   la pregunta abierta. Desaparece `choose_option` como turno especial.

Entregable: aclaración, respuesta corta y continuación funcionan en un test de tres
turnos con `FakeProvider`.

## Fase 4 · TUI

1. Burbuja de respuesta viva: al llegar el primer `TEXT_DELTA` del turno se monta un
   widget `Markdown` que se actualiza con los deltas agrupados. Al terminar se cierra
   con el pie de métricas actual (`render.reply_panel`).
2. El log deja de pintar eventos de ciclo de vida (`task_started`, `graph_built`,
   `node_*`). Las tools se muestran como líneas compactas de una sola fila, con la
   tarjeta completa (`render.action_card`) plegada o en el panel de agentes.
3. Delegaciones: una línea «→ python_coder: <instrucción resumida>» y su resultado.
   El panel lateral de agentes se mantiene tal cual.
4. Preguntas: si la respuesta trae `choices`, se pintan numeradas bajo la burbuja.
   Escribir `1`, `2` o el texto libre responde. Se retira `PlanOptionsScreen`.
5. Estado de espera honesto: «pensando…» hasta el primer delta, luego el nombre de la
   tool o del agente en curso.
6. Opcional: `Input` pasa a `TextArea` con historial de mensajes en ↑/↓.

## Fase 5 · App de escritorio

Backend (`satellite/desktop/backend.py`, `api.py`, `events.py`):

1. Cerrojo por proyecto en `send`: si hay un turno vivo en `_turnos[key]`, se
   responde `409 turn_running`. Arregla la carrera de cancelación.
2. `/status` expone `turn: {running, task_id, started_at}` para que una recarga a
   mitad de turno muestre el estado real.
3. `PendingApprovalPort.request` (`approval/base.py:78`) emite un evento
   `APPROVAL_REQUESTED` y otro `APPROVAL_RESOLVED`. La UI deja de sondear cada 1,5 s.
4. Respetar el cursor `after` del SSE (`electron/main.ts:274` lo fuerza a 0) y
   reconectar automáticamente con ese cursor antes de declarar el backend caído.

Renderer (`desktop/src/App.tsx`, `Chat.tsx`, `turnActivity.ts`):

1. `enviar()` (`App.tsx:517`) usa el `text`, `status` y `output` del POST para cerrar
   la burbuja al instante. `cargarTodo()` se ejecuta después y en segundo plano.
2. Burbuja del asistente en curso, creada al primer `TEXT_DELTA` del `task_id` actual
   y alimentada por SSE. Se reconcilia con el turno persistido al final.
3. La línea de estado filtra los eventos por el `task_id` del turno en curso, nunca
   el último evento global (`Chat.tsx:443`).
4. Preguntas con `choices` como botones bajo la burbuja, más «otra respuesta» que
   enfoca el compositor. Sustituye la tarjeta de plan y su estado triplicado.
5. Burbujas con `status: needs_user_input` con estilo propio de pregunta.
6. «Pedir cambios» en una aprobación abre un campo de texto que viaja en `feedback`.
7. `ocupado` se libera solo cuando el POST resuelve, no en el `finally` de cancelar.
8. Avisos por turno en vez de una única línea global.
9. El cajón de cambios ya no se abre solo al terminar; muestra un indicador.

## Fase 6 · Retiro del legado

Cuando las fases 1-5 estén estables y `pilot` sea el valor por defecto durante una
versión:

- Eliminar la cascada de `DefaultOrchestrator.execute` y `_answer_direct` con sus tres
  reintentos (`orchestrator.py:610-855`).
- Eliminar el enrutado de `chat_detector.py`, `diagram_intent.is_diagram_request`,
  `desktop_flow.py`, `web_research_flow.py` y `diagram_flow.py` como rutas.
  `extract_mermaid` y el renderizado de diagramas se quedan.
- `catalog_answer.py` pasa a ser una tool de solo lectura del piloto,
  `describe_runtime`, que responde agentes, skills, tools y modelo desde el registro.
- Retirar `LLMPlanner`, `PlanOptionGenerator`, `orchestrator.*.txt` y
  `plan_options.*.txt`, y la bandera `legacy`.
- Ajustar o borrar los tests acoplados a la ruta antigua: `test_chat_detector.py`,
  `test_orchestrator_chat.py`, `test_catalog_answer.py`, `test_planner_llm.py`,
  `test_plan_mode.py`, `test_orchestrator_*`, `test_diagram_intent.py`,
  `test_desktop_flow.py`, `test_web_research_flow.py`.
- Actualizar el README: tabla de agentes base y descripción del flujo.

## Fase 7 · Evaluación

1. `scripts/eval_flow.py`: ejecuta contra un proveedor real los mensajes de la tabla
   de la auditoría y otros casos frecuentes, y registra qué tools y delegaciones usó
   el piloto, la latencia hasta el primer delta y los tokens.
2. Casos mínimos: preguntas sobre código, «¿qué modelo de datos usa el proyecto?»,
   aclaración seguida de «el segundo», «hazlo» tras una propuesta, un cambio con
   tests, una búsqueda web, un diagrama y una acción de escritorio con aprobación.
3. Se ejecuta a mano antes de cambiar el valor por defecto de la bandera. No entra en
   CI porque necesita credenciales.

---

## Orden y dependencias

| Fase | Depende de | Tamaño estimado |
|---|---|---|
| 0 Red de seguridad | ninguna | pequeño |
| 1 Streaming | 0 | medio |
| 2 Piloto | 0 | grande |
| 3 Preguntas abiertas | 2 | medio |
| 4 TUI | 1, 3 | medio |
| 5 Escritorio | 1, 3 | grande |
| 6 Retiro del legado | 2-5 estables | medio |
| 7 Evaluación | 2, 3 | pequeño |

Las fases 1 y 2 pueden avanzar en paralelo. Los arreglos del backend de escritorio de
la fase 5 (cerrojo, `/status`, eventos de aprobación, cursor SSE) no dependen del
piloto y pueden hacerse ya.

## Decisiones abiertas

- **D1 · Escalado de permisos del piloto.** Propuesta: el piloto es raíz de sesión y no
  dispara `permission_escalation` al delegar en agentes del catálogo base. Los agentes
  creados por el usuario siguen pidiendo aprobación la primera vez.
- **D2 · ¿Puede el piloto editar por sí mismo?** Propuesta: no. Mantiene la identidad
  microagéntica y la separación entre decidir y escribir. El coste es un bucle extra
  de agente en cambios triviales. Alternativa: permitir `edit_file` al piloto para
  ediciones de un solo archivo y delegar el resto.
- **D3 · Modelos pequeños sin buen uso de tools.** Propuesta: si
  `model_capabilities` indica que el modelo del rol `orchestrator` no soporta tools,
  `build_runtime` cae a `legacy` y lo dice en `doctor`. Hay que decidir si se mantiene
  el legado para ese caso o se exige un modelo con tools.
- **D4 · MCP.** El servidor MCP ejecuta el mismo piloto con aprobación por
  elicitación y sin `desktop_operator`. `ask_user` se traduce a elicitación si el
  cliente la soporta y, si no, a una respuesta de texto con la pregunta.

## Riesgos

- **Coste y latencia.** Un saludo sigue costando una sola llamada. Una pregunta sobre
  código cuesta más llamadas que hoy, pero hoy esa respuesta se inventa. El catálogo y
  las tools van en el prefijo estable, así que la caché de prompt amortiza el piloto.
  Los tests de determinismo de prefijo (`test_prefix_determinism.py`) deben cubrir el
  nuevo system prompt.
- **Regresión de idioma.** Las reglas de idioma de salida (`OUTPUT_LANGUAGE_RULE`) se
  incluyen en el prompt del piloto y se prueban con los tests de `test_output_language.py`.
- **Doble descomposición.** Con el piloto delegando y los coders usando `spawn_agent`,
  hay dos niveles. Se mantiene el límite de profundidad de `DelegationContext` y se
  prohíbe que un agente delegado vuelva a llamar al piloto.
