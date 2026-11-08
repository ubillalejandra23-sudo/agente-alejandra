# Red de agentes A2A · Auditoría CMF/SII + IA aplicada + Marketing

Taller *Implementa y entrega tu agente* (Agentic AI · Curso 2). Un único servicio **FastAPI** que expone tres agentes
de IA que **colaboran entre sí por el protocolo A2A**, con acceso protegido, Swagger y una consola web de chat.

| | |
|---|---|
| **Opciones elegidas** | **1 · Servicio FastAPI** con acceso protegido (Swagger en `/docs`) **y 2 · Canal de atención: página web con frontend de chat** (`/`) |
| **Problema que resuelve** | Una consulta que cruza especialidades (p. ej. *¿cumple esta empresa la normativa?, ¿conviene automatizar su revisión con IA?, ¿cómo lo posiciono comercialmente?*) se reparte entre los agentes correctos, que se consultan por A2A, y se entrega **una conclusión integrada + el aporte de cada agente sin modificar**. |
| **Entrega** | Local reproducible. Despliegue opcional en Heroku (ver abajo). |

## Integrantes y aportes

| Agente | Autor/a | Aporte |
|---|---|---|
| `agente_alejandra` | **Alejandra Ubilla** | Perfil, prompts y 9 herramientas de auditoría (SII, CMF, fondos, checklists, apuntes). Además: arquitectura del servicio, orquestador A2A, seguridad, API, interfaz web, pruebas y despliegue. |
| `agente_bryhan` | **Bryhan Cortes** | Perfil, prompt y 3 herramientas (conocimientos, comparar opciones, diseñar plan) de AI Engineering aplicado a procesos empresariales y financieros. |
| `agente_carlos` | **Carlos Monroy** | Perfil, prompt y herramienta de apuntes de ingeniería comercial y marketing. |

> Los agentes de Bryhan y Carlos provienen de sus entregas del taller anterior (A2A nativo con LangGraph + Gemini). Aquí se
> **portaron a un solo servicio y al mismo modelo (Claude)** conservando sus prompts, herramientas y habilidades.

## Qué se reutiliza del trabajo anterior
Los tres agentes (perfil, prompt, herramientas, Agent Card) y el protocolo **A2A** (`message/send`, `contextId` como
memoria de conversación). Cambió la forma de servirlos: antes un `langgraph dev` por agente; ahora **un FastAPI** con el
SDK oficial `a2a-sdk`, y las rutas originales de Alejandra (`/a2a`, `/.well-known/agent-card.json`) siguen vigentes.

## Arquitectura

```
                     ┌────────────────────── FastAPI (un solo servicio) ──────────────────────┐
Navegador (/)  ──▶   │  /api/consulta/stream  ─▶ Orquestador                                  │
Swagger (/docs) ──▶  │  /api/consulta              1) enrutador (LLM) elige agentes           │
Cliente HTTP    ──▶  │  /chat (un agente)          2) llamadas A2A reales (Agent Card +       │
                     │                                message/send, X-API-Key, X-Request-ID)  │
Otro agente A2A ──▶  │  /agents/{id}/a2a  ◀────────   en paralelo o en secuencia              │
                     │  /agents/{id}/.well-known/     3) conclusión integrada + aportes       │
                     │      agent-card.json              sin modificar + verificación de cifras│
                     └──────────┬───────────────┬───────────────┬─────────────────────────────┘
                          agente_alejandra  agente_bryhan   agente_carlos   (+ agentes externos A2A_EXTERNOS)
```

**Fidelidad de las conclusiones** (datos, cálculos, unidades y supuestos):
- El aporte de **cada agente se entrega sin modificar** debajo de la conclusión; el modelo solo redacta la conclusión y tiene prohibido alterar cifras.
- Se **verifica** que toda cifra de la conclusión exista en los aportes o en la consulta; si no, se muestra una advertencia (`verificacion.cifras_no_respaldadas`).
- Si un agente falla se informa como falla (`estado: error`); si fallan todos, la API responde **502**. Si un agente pide datos, se transmiten como *Datos que faltan*.
- Lo que devuelve otro agente se pasa al modelo como **dato** entre etiquetas, nunca como instrucción.

**Memoria persistente e historial:** la memoria de cada agente (por conversación) y el historial de consultas se guardan en **Postgres** si existe `DATABASE_URL` (Heroku), en **SQLite local** (`data/agentes.db`) en otro caso, o solo en memoria con `PERSISTENCIA=memoria`. `GET /health` informa el backend activo (`persistencia`). Sobreviven a reinicios y despliegues; la consola web tiene un botón **Historial** para retomar conversaciones. Si la base falla al arrancar, el servicio sigue en memoria y lo registra en el log (no se presenta como persistente).

**Diagrama del flujo A2A:** la consola dibuja en vivo cómo viaja la consulta (Usuario → Enrutador → agentes → Síntesis → Respuesta): nodos que se activan, aristas animadas, tiempos por agente, agentes no consultados atenuados y errores en rojo. En modo secuencial muestra la cadena entre agentes.

**Colaboración A2A:** modo *paralelo* (todos a la vez) o *secuencial* (cada agente recibe como dato los aportes de los anteriores y puede señalar contradicciones). El `contextId` A2A es estable por conversación y agente (memoria). Se pueden sumar agentes de otras personas con `A2A_EXTERNOS`.

## Endpoints

| Endpoint | Acceso | Descripción |
|---|---|---|
| `GET /` | público | Consola web de chat (sin claves en el código; la clave se escribe en el navegador y se guarda solo en esa pestaña) |
| `GET /docs` | público | Swagger UI (botón **Authorize**) |
| `GET /health` · `GET /api/agentes` | público | Estado y catálogo de agentes |
| `GET /agents/{id}/.well-known/agent-card.json` | público | Agent Card A2A de cada agente (declara que requiere `X-API-Key`) |
| `POST /api/consulta` | **clave** | Consulta colaborativa (respuesta completa con trazabilidad). En Heroku usar `/stream` si la consulta puede pasar de 30 s |
| `POST /api/consulta/stream` | **clave** | Igual, con progreso en vivo (NDJSON); lo usa la consola web |
| `GET /api/historial` · `GET /api/historial/{solicitud_id}` | **clave** | Consultas guardadas (con `?conversation_id=…&detalle=true` devuelve una conversación completa) |
| `POST /chat` | **clave** | Consulta directa a un agente (`"agente": "agente_bryhan"`…) |
| `POST /agents/{id}/a2a` | **clave** | JSON-RPC A2A de cada agente (`/a2a` = Alejandra, ruta original) |

**Seguridad:** clave validada en el servidor (`X-API-Key` o `Authorization: Bearer`, comparación en tiempo constante). Sin `API_KEYS` el servidor **rechaza todo con 503** (falla cerrado). Límite de consultas por minuto y por clave (`RATE_LIMIT_PER_MIN`, error 429). Las claves viven solo en variables de entorno / `.env` (ignorado por git); el frontend no contiene ninguna.
**Errores claros:** 401 sin clave · 422 entrada inválida · 429 límite · 502 falla de proveedor o de todos los agentes.
**Trazabilidad:** cada consulta recibe un `solicitud_id` que viaja como `X-Request-ID` en cada llamada A2A y aparece en los logs del servidor (`consulta rid=…`, `A2A recibido … rid=…`, `a2a rid=… agente=… ms=…`) y en la traza de la consola web.

## Preparación manual (lo que debe tener el evaluador)
1. **Python 3.11+** (probado con 3.12) e internet.
2. **Una clave de Anthropic** (`ANTHROPIC_API_KEY`) con crédito: https://console.anthropic.com. Cada consulta consume tokens (una consulta de 3 agentes hace ~5 llamadas al modelo: enrutador, 3 agentes y conclusión).
3. Elegir una `API_KEYS` propia (texto largo y aleatorio).
4. No se usan cuentas de Gmail/Telegram/WhatsApp ni se envían mensajes: **no hay acciones con efectos externos**; los datos SII/CMF/fondos son simulados. Para detener los procesos: `Ctrl+C` (local) o `heroku ps:scale web=0` (nube).

## Instalación

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # edite .env: ANTHROPIC_API_KEY y API_KEYS
```
```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
Genere una clave de acceso: `python -c "import secrets; print(secrets.token_urlsafe(32))"` y póngala en `API_KEYS`.

## Iniciar, probar y detener

```bash
uvicorn app.main:app --port 8000          # iniciar (Ctrl+C para detener)
# puerto/host configurables:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Consola web:** http://127.0.0.1:8000 → *Clave API* → pegue su clave → elija agentes (o ninguno = automático) y consulte.
**Swagger:** http://127.0.0.1:8000/docs → **Authorize** → `POST /api/consulta` → *Try it out*.

**Línea de comandos** (Git Bash/macOS/Linux; en PowerShell use `curl.exe`):
```bash
# Acceso permitido: consulta colaborativa (el enrutador elige los agentes)
curl -X POST http://127.0.0.1:8000/api/consulta -H "X-API-Key: SU_CLAVE" -H "Content-Type: application/json" \
     -d '{"mensaje":"¿Cumple la empresa 77.987.654-3 con la normativa y conviene automatizar su revisión con IA?"}'
# Acceso rechazado (401)
curl -i -X POST http://127.0.0.1:8000/api/consulta -H "Content-Type: application/json" -d '{"mensaje":"hola"}'
# Agent Card A2A de un agente
curl http://127.0.0.1:8000/agents/agente_bryhan/.well-known/agent-card.json
# A2A message/send directo a un agente
curl -X POST http://127.0.0.1:8000/agents/agente_alejandra/a2a -H "X-API-Key: SU_CLAVE" -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{
  "role":"user","messageId":"m1","contextId":"6f1c2a3e-0b7d-4c1e-9a55-2b8f0c1d7e90",
  "parts":[{"kind":"text","text":"¿Qué observaciones tiene en el SII el RUT 77.987.654-3?"}]}}}'
```
Datos de prueba: RUT `76.123.456-7`, `77.987.654-3`, `78.555.111-2`; fondos `FM Acciones Chile`, `FM Deuda Privada`, `FM Inmobiliario`; tipos de empresa `sa`/`spa`/`ltda`.

**Evaluación automática de fidelidad** (21 casos: cifras y cálculos exactos, datos faltantes, no inventar, fuera de alcance, inyección de instrucciones, enrutamiento, colaboración A2A y seguridad):
```bash
python -m evaluacion.evaluar                       # contra http://127.0.0.1:8000 (usa la 1ª clave de API_KEYS en .env)
python -m evaluacion.evaluar --url https://<app>.herokuapp.com --key <clave> --entorno HEROKU
python -m evaluacion.evaluar --sin-modelo          # solo seguridad/validación (gratis)
```
Genera `evidencias/resultados/<fecha>-<entorno>/REPORTE.md` en el formato del taller (entorno y fecha, solicitud, resultado esperado, obtenido, observaciones) y `resultados.json`; nunca escribe la clave. Las comprobaciones buscan cifras y términos exactos: una respuesta correcta redactada distinto puede marcarse como fallo y debe revisarse a mano.

**Pruebas automáticas:**
```bash
pip install -r requirements-dev.txt
pytest -q tests/test_api.py     # seguridad, validación, Agent Cards, límites (sin modelo, gratis)
pytest -q tests/test_persistencia.py  # SQLite y Postgres embebido: el historial y la memoria sobreviven a un reinicio
pytest -q tests/test_e2e.py     # extremo a extremo con modelo real y servidor (consume tokens; requiere ANTHROPIC_API_KEY)
```

### Agentes externos (opcional)
Para sumar el agente A2A de otra persona, defina en `.env` `A2A_EXTERNOS` (JSON en una línea, ver `.env.example`) con su `card_url` y, si la exige, su `api_key`. Aparece en el selector y colabora con los locales.

## Evidencias
Carpeta `evidencias/` (ver `evidencias/README.md` con el guion):
- `resultados/<fecha>-<entorno>/REPORTE.md` — generado por `python -m evaluacion.evaluar`: cada caso con **entorno y fecha, solicitud, resultado esperado, resultado obtenido y observaciones**, trazabilidad (`solicitud_id`, llamadas A2A con tiempos y contextId) y comprobaciones.
- `capturas/` — capturas de la consola web (opcional: `python evidencias/capturar_ui.py` las genera con el navegador instalado). Las del acceso rechazado (401) y las de Swagger se toman a mano siguiendo el guion.
- Log del servidor: `uvicorn app.main:app --port 8000 2>&1 | tee evidencias/log-local.txt` y busque `rid=<solicitud_id>` para relacionar la solicitud con cada llamada A2A.

## Límites conocidos
- Datos SII/CMF/fondos **simulados**; los agentes solo recomiendan, no ejecutan ni informan a organismos.
- Las tareas A2A viven en memoria del proceso (la memoria de los agentes y el historial sí se persisten). Un solo proceso.
- Con Postgres en **Windows** local, `psycopg` no funciona con el bucle asyncio por defecto; el servicio cae a SQLite con un aviso (Heroku/Linux no tiene este problema).
- El historial guarda las preguntas y respuestas en la base (datos de prueba, sin datos personales); proteja la clave y la base como cualquier registro de consultas.
- El límite de consultas protege `/chat` y `/api/consulta`; los endpoints A2A directos están protegidos por clave pero sin límite de tasa.
- La verificación de cifras detecta valores ausentes en los aportes; no valida el *razonamiento* de la conclusión (por eso los aportes se entregan completos).
- A2A: respuesta inmediata (sin streaming ni cancelación de tareas). Los agentes de Bryhan y Carlos pasaron de Gemini a Claude: su comportamiento puede diferir levemente del original.

## Despliegue en Heroku (opcional, +1 punto)
Archivos incluidos: `Procfile` (comando de arranque), `.python-version`, `requirements.txt`. El puerto lo entrega Heroku en `$PORT`.

```bash
heroku login
heroku create nombre-unico-app
heroku config:set ANTHROPIC_API_KEY=sk-ant-... API_KEYS=<clave-larga> PUBLIC_URL=https://nombre-unico-app.herokuapp.com
git push heroku main
heroku ps:scale web=1         # un solo dyno
heroku logs --tail            # evidencias del entorno remoto
heroku ps:scale web=0         # detener (evita costos)
```
Pruebe `https://<app>.herokuapp.com/` y `/docs`, y entregue al evaluador la URL y una clave por una vía privada.
**Datos que se conservan:** con el complemento **Heroku Postgres** (`heroku addons:create heroku-postgresql:essential-0`, de pago) el servicio lo detecta por `DATABASE_URL` y conserva la memoria de los agentes y el historial entre reinicios y despliegues. Sin él, usa SQLite en el disco efímero del dyno (se borra al reiniciar, al menos una vez al día). **Servicios externos:** API de Anthropic (y los agentes de `A2A_EXTERNOS`, si se configuran). La respuesta se entrega en *streaming* para no chocar con el límite de 30 s de Heroku.

## Estructura
```
app/
  main.py          FastAPI: endpoints, protección de A2A, consola web
  orquestador.py   enrutador, llamadas A2A, síntesis y verificación de cifras
  a2a_server.py    Agent Card y endpoint A2A por agente (a2a-sdk)
  a2a_cliente.py   cliente A2A (descubre la tarjeta y envía message/send)
  almacen.py       persistencia: Postgres / SQLite / memoria (checkpointer de LangGraph + historial)
  agentes/         registro.py · alejandra/ · bryhan/ · carlos/  (prompts.py + herramientas.py)
  security.py      clave de acceso y límite de consultas
  config.py        variables de entorno
  static/          index.html · styles.css · app.js  (consola web sin dependencias externas)
evaluacion/        casos.py · evaluar.py (21 casos de fidelidad, A2A y seguridad → reporte)
evidencias/        README.md (guion) · resultados/ · capturas/ · capturar_ui.py
tests/             test_api.py · test_persistencia.py · test_e2e.py (modelo real)
Procfile  .python-version  requirements*.txt  .env.example
```
