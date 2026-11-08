# Evidencias de ejecución

Todo lo que hay aquí viene de ejecuciones reales (no de un diagrama ni de respuestas fijas). Cada caso sigue el formato del
taller: **entorno y fecha · solicitud · resultado esperado · resultado obtenido · observaciones**.

## Qué hay

| Ruta | Qué es | Cómo se genera |
|---|---|---|
| `resultados/<fecha>-local/REPORTE.md` | 25 casos ejecutados contra el servidor **local**: fidelidad de cifras, datos faltantes, no inventar, fuera de alcance, inyección de instrucciones, Coordinador (saludos y preguntas generales), enrutamiento, colaboración A2A y seguridad (acceso permitido/rechazado) | `python -m evaluacion.evaluar` |
| `resultados/<fecha>-heroku/REPORTE.md` | Los mismos casos contra el **despliegue en Heroku** (evidencia del entorno remoto) | `python -m evaluacion.evaluar --url https://<app>.herokuapp.com --key <clave> --entorno HEROKU` |
| `resultados/*/resultados.json` | Datos crudos: respuesta completa, `solicitud_id`, agentes llamados por A2A, tiempos y contextId | (idem) |
| `capturas/*.png` | Recorrido de la consola web: inicio, consulta en curso (diagrama animado), resultado y traza | `python evidencias/capturar_ui.py` |

La clave de acceso **no se escribe** en ningún reporte ni captura.

## Cómo leer la trazabilidad
Cada consulta colaborativa tiene un `solicitud_id`. Aparece en el reporte, en la traza de la consola web, en
`GET /api/historial/{solicitud_id}` y en el log del servidor, donde se ve toda la cadena:

```
orquestador consulta rid=<id> modo=paralelo auto=False agentes=['agente_alejandra', 'agente_bryhan']
api A2A recibido path=/agents/agente_alejandra/a2a rid=<id>        <- el agente recibió la llamada A2A con ese id
orquestador a2a rid=<id> agente=agente_alejandra ctx=<contextId> estado=ok ms=8375
orquestador consulta rid=<id> completada ok=2/2 ms=19466
```
Para guardar el log: `uvicorn app.main:app --port 8000 2>&1 | tee evidencias/log-local.txt` (en Heroku: `heroku logs --tail`).

## Guion para las capturas que se toman a mano
Complementan las automáticas. Oculte la clave (use el botón *Authorize* de Swagger o la consola web: nunca se muestra).

1. **Acceso rechazado (401):** Swagger → `POST /api/consulta` *sin* Authorize → *Execute* → captura del `401`.
2. **Acceso permitido:** Authorize con su clave → el mismo `POST /api/consulta` con
   `{"mensaje": "¿Cumple la empresa 77.987.654-3 con la normativa y conviene automatizar su revisión con IA?"}` → captura de la
   respuesta (conclusión integrada + aportes, `solicitud_id`, `agentes[].ms`).
3. **Colaboración A2A:** consola web, elegir 2 o 3 agentes, enviar la consulta; captura con el diagrama y la traza visibles.
4. **Saludo / pregunta general:** consola web sin elegir agentes: `hola` y `¿qué puedes hacer?` → responde el *Coordinador* (el diagrama lo muestra y ningún especialista es consultado).
5. **Dato faltante:** consola web con *Alejandra* sola: `¿Cumple mi empresa con la normativa?` → debe pedir el RUT.
6. **Log:** captura de la terminal de uvicorn con las líneas `consulta rid=…` / `a2a rid=…` de una de esas consultas.
7. **Agent Card:** `GET /agents/agente_bryhan/.well-known/agent-card.json` en el navegador.
8. **Telegram — acceso permitido:** desde su cuenta autorizada escriba al bot `¿Cumple la empresa 77.987.654-3 con la normativa?` →
   captura de la solicitud y de la respuesta del bot (y del log `telegram consulta … / orquestador a2a rid=…`).
9. **Telegram — acceso rechazado:** desde OTRA cuenta de Telegram escriba al bot → debe responder «Acceso no autorizado» (captura).
10. **Historial persistente:** reinicie el servidor (`Ctrl+C` y volver a iniciar), abra **Historial** en la consola y retome una conversación.

## Registro de cada captura (copie y complete)

| Dato | Registro |
|---|---|
| Entorno y fecha | LOCAL / HEROKU · AAAA-MM-DD |
| Solicitud | |
| Resultado esperado | |
| Resultado obtenido (y archivo de la captura) | |
| Observaciones (diferencias, errores, límites, pendientes) | |

> Los datos de SII/CMF/fondos son simulados. Una ejecución local no se rotula como producción.
