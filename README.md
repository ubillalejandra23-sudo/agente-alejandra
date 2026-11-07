# Agente Alejandra · Auditoría CMF/SII — Servicio FastAPI con A2A

Taller *Implementa y entrega tu agente* (Agentic AI · Curso 2).

| | |
|---|---|
| **Integrante** | Alejandra Ubilla (trabajo individual) |
| **Aporte** | Diseño del perfil, prompts, herramientas de auditoría (SII/CMF/fondos), API FastAPI, seguridad y colaboración A2A |
| **Opción elegida** | **1 · Servicio FastAPI** con acceso protegido, documentado con Swagger (`/docs`) |
| **Problema que resuelve** | Auditar el cumplimiento normativo y tributario de empresas (cruzando SII y CMF) y de fondos regulados por la CMF (concentración, ratios, checklist), desde una API que otro sistema o agente puede consumir. |
| **Entrega** | Local reproducible. Despliegue opcional en Heroku (ver más abajo). |

## Qué se reutiliza del taller anterior
Todo el agente: perfil y prompt (`app/agente/prompts.py`), las 9 herramientas con datos simulados (`app/agente/herramientas.py`) y la conexión **A2A**. Cambió la forma de servirlo: antes lo exponía `langgraph dev`; ahora lo expone **FastAPI** con el SDK oficial `a2a-sdk`, por lo que el protocolo A2A (Agent Card + JSON-RPC `message/send`) sigue siendo el mismo.

## Arquitectura

```
Cliente ──X-API-Key──▶ FastAPI ─┬─ POST /chat ─────────┐
                                ├─ POST /a2a (A2A) ────┼──▶ Agente Alejandra (LangChain + Claude)
Otro agente A2A ────────────────┘                      │        ├─ herramientas SII / CMF / fondos / apuntes
                                                       │        └─ herramienta A2A ──▶ agente par (opcional)
GET /.well-known/agent-card.json (pública)   GET /docs (Swagger)   GET /health
```

| Endpoint | Acceso | Descripción |
|---|---|---|
| `GET /health` | público | Estado del servicio |
| `GET /.well-known/agent-card.json` | público | Agent Card A2A (declara que se requiere `X-API-Key`) |
| `POST /chat` | **clave** | Consulta directa: `{"mensaje": "...", "conversation_id": "opcional"}` |
| `POST /a2a` | **clave** | JSON-RPC A2A (`message/send` y `SendMessage`). El `contextId` es la memoria de la conversación |
| `GET /docs` | público | Swagger UI (botón **Authorize** para ingresar la clave) |

**Seguridad:** la clave se valida en el servidor (comparación en tiempo constante) vía `X-API-Key` o `Authorization: Bearer`. Si `API_KEYS` no está configurada el servidor **rechaza todo con 503** (falla cerrado). Las claves viven solo en variables de entorno / `.env` (ignorado por git).

**Errores claros:** 401 sin clave o clave mala · 422 entrada inválida (vacía, >4000 caracteres, id con caracteres no permitidos) · 502 si falla el proveedor del modelo · si el agente par A2A no responde, el agente lo informa y no inventa su respuesta.

**Fidelidad en A2A:** lo que responde el agente par llega al modelo entre `<respuesta_agente_remoto>` como *dato*, y el prompt exige conservar cifras, unidades, supuestos y condiciones y atribuir cada dato a su fuente.

## Preparación manual (lo que debe tener el evaluador)
1. **Python 3.11+** (probado con 3.12) y conexión a internet.
2. **Una clave de Anthropic** (`ANTHROPIC_API_KEY`) con crédito: https://console.anthropic.com. Cada consulta consume tokens del modelo (`MODELO`, por defecto `claude-sonnet-5-5`).
3. Elegir una `API_KEYS` propia (cualquier texto largo y aleatorio). No se usan cuentas de Gmail/Telegram/WhatsApp ni se envían mensajes reales: **no hay acciones con efectos externos**; los datos SII/CMF/fondos son simulados.

## Instalación

```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # luego edite .env con su ANTHROPIC_API_KEY y su API_KEYS
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

**Probar en Swagger:** abra http://127.0.0.1:8000/docs → **Authorize** → pegue su clave → `POST /chat` → *Try it out*.

**Probar por línea de comandos** (Git Bash/macOS/Linux; en PowerShell use `curl.exe`):
```bash
# Acceso permitido
curl -X POST http://127.0.0.1:8000/chat -H "X-API-Key: SU_CLAVE" -H "Content-Type: application/json" \
     -d '{"mensaje":"¿Cumple con la normativa la empresa 77.987.654-3?"}'
# Acceso rechazado (401)
curl -i -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d '{"mensaje":"hola"}'
# Agent Card A2A
curl http://127.0.0.1:8000/.well-known/agent-card.json
# A2A message/send
curl -X POST http://127.0.0.1:8000/a2a -H "X-API-Key: SU_CLAVE" -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{
  "role":"user","messageId":"m1","contextId":"6f1c2a3e-0b7d-4c1e-9a55-2b8f0c1d7e90",
  "parts":[{"kind":"text","text":"¿Qué observaciones tiene en el SII el RUT 77.987.654-3?"}]}}}'
```
Datos de prueba: RUT `76.123.456-7`, `77.987.654-3`, `78.555.111-2`; fondos `FM Acciones Chile`, `FM Deuda Privada`, `FM Inmobiliario`; tipos de empresa `sa`/`spa`/`ltda`.

**Pruebas automáticas:** `pip install -r requirements-dev.txt && pytest -q`. Las de seguridad/validación no llaman al modelo; las 2 pruebas con modelo real se omiten si no hay `ANTHROPIC_API_KEY`.

### Colaboración A2A con un agente par (opcional)
Para que Alejandra consulte a otro agente A2A (p. ej. Benjamín, control interno), defina en `.env`:
`A2A_PEER_CARD_URL` (URL completa de la Agent Card del par), `A2A_PEER_NAME` y, si el par exige clave, `A2A_PEER_API_KEY`. Sin `A2A_PEER_CARD_URL` la herramienta no se registra. Puede probarlo levantando una segunda instancia de este mismo proyecto en otro puerto (`API_KEYS` y `PUBLIC_URL` distintos) y apuntando a ella.

## Evidencias (completar con capturas propias)
Guárdelas en `evidencias/` (sin claves visibles) y describa cada caso con: **entorno y fecha (LOCAL)**, **solicitud**, **resultado esperado**, **resultado obtenido**, **observaciones**. Casos mínimos:
1. `POST /chat` autorizado en Swagger con su respuesta.
2. Intento rechazado (401) sin clave o con clave falsa.
3. Registro de la consola de uvicorn mostrando la solicitud (`chat conversation=...` / `A2A message context=...`).
4. (Si usa par) llamada A2A entre agentes y la respuesta final integrada.

## Límites conocidos
- Datos SII/CMF/fondos **simulados**; el agente solo recomienda, no ejecuta ni informa a organismos.
- La memoria de conversación y las tareas A2A están **en memoria del proceso**: se pierden al reiniciar.
- Un solo proceso/worker (necesario para que la memoria en proceso sea coherente).
- Sin límite de tasa (rate limiting): quien tenga la clave puede generar gasto en su cuenta del modelo.
- A2A: respuesta inmediata (sin streaming ni cancelación de tareas).

## Despliegue en Heroku (opcional, +1 punto)
Archivos ya incluidos: `Procfile` (comando de arranque), `.python-version`, `requirements.txt`. El puerto lo entrega Heroku en `$PORT`.

```bash
heroku login
heroku create nombre-unico-app
heroku config:set ANTHROPIC_API_KEY=sk-ant-... API_KEYS=<clave-larga> PUBLIC_URL=https://nombre-unico-app.herokuapp.com
git push heroku main          # o: git push heroku <rama>:main
heroku ps:scale web=1         # un solo dyno
heroku logs --tail            # evidencias del entorno remoto
heroku ps:scale web=0         # detener (evita costos)
```
Pruebe luego `https://nombre-unico-app.herokuapp.com/docs` y entregue al evaluador la URL y una clave por una vía privada.

**Datos que se conservan:** ninguno en disco. Lo único con estado es la memoria de conversación (RAM); Heroku reinicia los dynos al menos una vez al día, así que las conversaciones se reinician. Para persistirlas habría que cambiar `InMemorySaver` por un checkpointer en Postgres (`heroku-postgresql`). **Servicios externos:** API de Anthropic (y el agente par A2A, si se configura).

## Estructura
```
app/
  main.py          FastAPI: /chat, /health, protección de /a2a
  a2a_server.py    Agent Card + servidor A2A (a2a-sdk)
  security.py      validación de la clave
  config.py        variables de entorno
  agente/          graph.py · prompts.py · herramientas.py · a2a_par.py (cliente A2A)
tests/             pruebas de seguridad, validación y flujo real
Procfile  .python-version  requirements*.txt  .env.example
```
