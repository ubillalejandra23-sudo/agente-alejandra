"""Red de agentes A2A (Alejandra · Bryhan · Carlos) sobre FastAPI. Interfaz web en `/`, Swagger en `/docs`."""
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from a2a.server.routes import fastapi_routes
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.a2a_server import es_ruta_rpc, rutas_a2a
from app.almacen import almacen
from app.agentes.registro import AGENTES, construir_agentes, responder
from app.orquestador import MODOS, ErrorConsulta, consultar, roster, stream
from app.security import clave_valida, exigir_clave, extraer_clave

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")
STATIC = Path(__file__).parent / "static"


# ── modelos ────────────────────────────────────────────────────────────────────────────────────────────────────────
class ChatIn(BaseModel):
    mensaje: str = Field(
        ..., min_length=1, max_length=4000,
        description="Consulta en lenguaje natural (máx. 4000 caracteres). Incluya el dato que necesita el agente "
                    "(RUT de la empresa 12.345.678-9, nombre del fondo, etc.). Si falta, el agente lo pedirá.",
        examples=["¿Cumple con la normativa la empresa 77.987.654-3?"])
    conversation_id: str | None = Field(
        None, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$",
        description="Identificador de la conversación. Reutilícelo para que el agente recuerde el contexto. "
                    "Si se omite, se crea una conversación nueva y se devuelve su id.",
        examples=["auditoria-andina-001"])
    agente: str = Field("agente_alejandra", description="Agente que responde (ver GET /api/agentes).",
                        examples=["agente_alejandra"])


class ChatOut(BaseModel):
    respuesta: str = Field(..., description="Respuesta del agente.")
    conversation_id: str = Field(..., description="Id de la conversación; envíelo de vuelta para continuarla.")
    agente: str = Field(..., description="Agente que respondió.")


class ConsultaIn(BaseModel):
    mensaje: str = Field(..., min_length=1, max_length=4000, description="Consulta para la red de agentes.",
                         examples=["¿Cumple la empresa 77.987.654-3 y conviene automatizar su revisión con IA?"])
    agentes: list[str] = Field(
        default_factory=list, max_length=10,
        description="Ids de los agentes a consultar. Vacío = **automático**: el enrutador elige los que aportan.",
        examples=[["agente_alejandra", "agente_bryhan"]])
    modo: Literal["paralelo", "secuencial"] = Field(
        "paralelo", description="`paralelo`: todos a la vez. `secuencial`: cada agente recibe como dato los aportes "
                                "de los anteriores.")
    conversation_id: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$",
                                        description="Id de conversación (memoria por agente).")


class AgenteInfo(BaseModel):
    id: str
    nombre: str
    especialidad: str
    local: bool = Field(..., description="True si corre en este servicio; False si es un agente A2A externo.")
    card_url: str = Field(..., description="Agent Card pública del agente.")
    ejemplos: list[str] = []


class AporteOut(BaseModel):
    id: str
    nombre: str
    estado: Literal["ok", "error"]
    ms: int = Field(..., description="Milisegundos que tardó la llamada A2A.")
    context_id: str = Field(..., description="contextId A2A (memoria de esa conversación en ese agente).")
    respuesta: str | None = None
    error: str | None = None


class ConsultaOut(BaseModel):
    solicitud_id: str = Field(..., description="Id para relacionar la solicitud con los logs y las llamadas A2A.")
    conversation_id: str
    modo: str
    automatico: bool
    motivo_ruta: str
    respuesta: str = Field(..., description="Conclusión integrada + aporte de cada agente SIN modificar (Markdown).")
    conclusion: str | None = Field(None, description="Solo la conclusión integrada (None si respondió un único agente).")
    agentes: list[AporteOut]
    verificacion: dict = Field(..., description="`cifras_no_respaldadas`: cifras de la conclusión ausentes en los aportes.")
    ms_total: int


class HealthOut(BaseModel):
    status: str = Field(..., examples=["ok"])
    agentes: list[str]
    persistencia: str = Field(..., description="Dónde se guarda la memoria y el historial: `postgres`, `sqlite` o `memoria` "
                                               "(esta última se pierde al reiniciar).")
    acceso_configurado: bool = Field(..., description="False si el servidor no tiene API_KEYS (rechazará todo con 503).")


class ErrorOut(BaseModel):
    detail: str


DESCRIPCION = """
## Red de agentes A2A · Auditoría CMF/SII · IA aplicada · Marketing

Servicio **FastAPI** que expone tres agentes de IA que **colaboran entre sí por el protocolo A2A**:

| Agente | Especialidad |
|---|---|
| `agente_alejandra` | Auditoría de cumplimiento normativo y tributario (CMF y SII), fondos de inversión |
| `agente_bryhan` | AI Engineering aplicado a procesos empresariales y financieros |
| `agente_carlos` | Ingeniería comercial y marketing |

Se pueden sumar agentes de otras personas con solo su Agent Card (variable `A2A_EXTERNOS`).

### Cómo funciona una consulta colaborativa (`POST /api/consulta`)
1. Un **enrutador** elige qué agentes aportan (o usted los elige).
2. Cada agente se consulta por **A2A real** (descubre su Agent Card y envía `message/send`), en paralelo o en secuencia.
3. Se entrega una **conclusión integrada** y, debajo, **el aporte de cada agente sin modificar**. Se verifica que las
   cifras de la conclusión existan en los aportes. Si un agente falla, se informa como falla.

### Autenticación
Los endpoints protegidos exigen una clave configurada en el servidor (`API_KEYS`). Pulse **Authorize** y escríbala, o
envíe `X-API-Key: <clave>` (también `Authorization: Bearer <clave>`). Sin clave → `401`; servidor sin claves → `503`.

### Datos y límites
Los datos de SII/CMF/fondos son **simulados**. Los agentes solo recomiendan: no ejecutan acciones ni envían mensajes.

### Códigos de error
`401` clave ausente/inválida · `422` entrada inválida · `429` demasiadas consultas · `502` falla de proveedor o de
todos los agentes · `503` acceso no configurado.
"""

TAGS = [
    {"name": "Estado", "description": "Salud del servicio y catálogo de agentes. Público, sin datos sensibles."},
    {"name": "Consulta colaborativa", "description": "Varios agentes colaborando por A2A con conclusión integrada. **Requiere clave.**"},
    {"name": "Historial", "description": "Consultas guardadas con su trazabilidad. **Requiere clave.**"},
    {"name": "Agente", "description": "Consulta directa a un único agente. **Requiere clave.** Memoria por `conversation_id`."},
    {"name": "A2A: Agent Card", "description": "Tarjeta pública de cada agente (habilidades, URL, seguridad) para que otros agentes A2A lo descubran."},
    {"name": "A2A: JSON-RPC", "description": "Endpoint A2A de cada agente (`message/send`). **Requiere clave** (`X-API-Key`)."},
]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Abre la base de datos y reconstruye los agentes con la memoria persistente."""
    await almacen.abrir()
    construir_agentes(almacen.checkpointer)
    yield
    await almacen.cerrar()


app = FastAPI(
    lifespan=lifespan,
    title="Red de agentes A2A · Auditoría CMF/SII",
    version="2.0.0",
    summary="Tres agentes colaborando por A2A, con API protegida, trazabilidad y conclusión verificada",
    description=DESCRIPCION, openapi_tags=TAGS, contact={"name": "Alejandra Ubilla"},
)
construir_agentes()
cards, rpc = rutas_a2a()


@app.middleware("http")
async def proteger_a2a(request: Request, call_next):
    """Los endpoints JSON-RPC A2A exigen la misma clave que la API (las Agent Cards quedan públicas)."""
    if es_ruta_rpc(request.url.path):
        if not config.API_KEYS:
            return JSONResponse({"detail": "Acceso deshabilitado: API_KEYS no configuradas."}, status_code=503)
        if not clave_valida(extraer_clave(request)):
            log.warning("A2A rechazado: clave ausente o inválida path=%s", request.url.path)
            return JSONResponse({"detail": "Clave de acceso ausente o inválida."}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        log.info("A2A recibido path=%s rid=%s", request.url.path, request.headers.get("x-request-id", "-"))
    return await call_next(request)


# ── estado y catálogo ──────────────────────────────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["Estado"], response_model=HealthOut, summary="Estado del servicio",
         description="Indica si el servicio está en línea, qué agentes locales tiene y si hay claves configuradas. "
                     "No requiere clave y no expone datos sensibles.")
async def health():
    return {"status": "ok", "agentes": list(AGENTES), "persistencia": almacen.backend,
            "acceso_configurado": bool(config.API_KEYS)}


@app.get("/api/agentes", tags=["Estado"], response_model=list[AgenteInfo], summary="Catálogo de agentes",
         description="Agentes que pueden consultarse (locales y externos A2A), con su especialidad, ejemplos de "
                     "consulta y la URL de su Agent Card. Público.")
async def agentes():
    salida = []
    for m in roster().values():
        local = AGENTES.get(m.id)
        salida.append(AgenteInfo(
            id=m.id, nombre=m.nombre, especialidad=m.especialidad, local=m.local, ejemplos=local.ejemplos if local else [],
            card_url=(f"{config.PUBLIC_URL}/agents/{m.id}/.well-known/agent-card.json" if m.local else m.card_url)))
    return salida


# ── consulta colaborativa ──────────────────────────────────────────────────────────────────────────────────────────
RESPUESTAS_CONSULTA = {401: {"model": ErrorOut, "description": "Clave ausente o inválida."},
                       422: {"description": "Entrada inválida (mensaje vacío o muy largo, agente o modo desconocido)."},
                       429: {"model": ErrorOut, "description": "Límite de consultas por minuto excedido."},
                       502: {"model": ErrorOut, "description": "Ningún agente pudo responder o falló el proveedor."},
                       503: {"model": ErrorOut, "description": "El servidor no tiene API_KEYS configuradas."}}


@app.post("/api/consulta", response_model=ConsultaOut, tags=["Consulta colaborativa"],
          dependencies=[Depends(exigir_clave)], summary="Consulta colaborativa entre agentes (A2A)",
          description="Consulta a varios agentes por A2A y devuelve una conclusión integrada más el aporte de cada "
                      "agente **sin modificar**, con trazabilidad (`solicitud_id`, tiempos, contextId A2A). "
                      "Sin `agentes` el enrutador elige los pertinentes. Si un solo agente participa, su respuesta se "
                      "entrega tal cual. Un agente que falla se informa como `error`; si fallan todos, responde 502.",
          responses=RESPUESTAS_CONSULTA)
async def api_consulta(body: ConsultaIn):
    try:
        return await consultar(body.mensaje, body.agentes, body.modo, body.conversation_id)
    except ErrorConsulta as e:
        raise HTTPException(e.status, e.detalle) from e


@app.post("/api/consulta/stream", tags=["Consulta colaborativa"], dependencies=[Depends(exigir_clave)],
          summary="Consulta colaborativa con progreso en vivo (NDJSON)",
          description="Igual que `/api/consulta`, pero responde un evento JSON por línea (`application/x-ndjson`): "
                      "`inicio`, `enrutado`, `agente` (uno por agente, apenas termina), `sintesis`, `final` o `error`, "
                      "y `ping` de mantenimiento. Es lo que usa la interfaz web.",
          responses={**RESPUESTAS_CONSULTA, 200: {"content": {"application/x-ndjson": {}}, "description": "Eventos NDJSON."}})
async def api_consulta_stream(body: ConsultaIn):
    async def lineas():
        async for ev in stream(body.mensaje, body.agentes, body.modo, body.conversation_id):
            yield json.dumps(ev, ensure_ascii=False) + "\n"
    return StreamingResponse(lineas(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})


# ── historial ──────────────────────────────────────────────────────────────────────────────────────────────────────
@app.get("/api/historial", tags=["Historial"], dependencies=[Depends(exigir_clave)], summary="Historial de consultas",
         description="Últimas consultas colaborativas guardadas (más recientes primero). Con `conversation_id` devuelve esa "
                     "conversación en orden cronológico; con `detalle=true` incluye respuestas y aportes completos. "
                     "**Requiere clave.** Las consultas se guardan en Postgres/SQLite según el entorno (ver `/health`).",
         responses={401: {"model": ErrorOut}, 503: {"model": ErrorOut}})
async def historial(conversation_id: str | None = Query(None, pattern=r"^[A-Za-z0-9_\-:.]+$", max_length=100),
                    limite: int = Query(30, ge=1, le=200), detalle: bool = False):
    return await almacen.listar(conversation_id, limite, detalle)


@app.get("/api/historial/{solicitud_id}", tags=["Historial"], dependencies=[Depends(exigir_clave)],
         summary="Detalle de una consulta", description="Consulta completa por su `solicitud_id` (el mismo que aparece en los logs "
         "y en las llamadas A2A como `X-Request-ID`). **Requiere clave.**",
         responses={401: {"model": ErrorOut}, 404: {"model": ErrorOut}, 503: {"model": ErrorOut}})
async def historial_detalle(solicitud_id: str):
    r = await almacen.obtener(solicitud_id)
    if not r:
        raise HTTPException(404, "No existe una consulta con ese solicitud_id.")
    return r


# ── agente directo ─────────────────────────────────────────────────────────────────────────────────────────────────
@app.post("/chat", response_model=ChatOut, tags=["Agente"], dependencies=[Depends(exigir_clave)],
          summary="Consultar a un agente",
          description="Envía una consulta a **un** agente (por defecto Alejandra), que decide qué herramientas usar y "
                      "responde en español. Si falta un dato lo pregunta en vez de inventarlo. Reenvíe el "
                      "`conversation_id` recibido para continuar la conversación.",
          responses={**RESPUESTAS_CONSULTA, 502: {"model": ErrorOut, "description": "Falló el proveedor del modelo; la consulta NO se realizó."}})
async def chat(body: ChatIn):
    if body.agente not in AGENTES:
        raise HTTPException(422, f"Agente desconocido: {body.agente}. Disponibles: {', '.join(AGENTES)}.")
    cid = body.conversation_id or uuid.uuid4().hex
    log.info("chat agente=%s conversation=%s chars=%d", body.agente, cid, len(body.mensaje))
    try:
        return ChatOut(respuesta=await responder(body.agente, body.mensaje, f"{cid}:{body.agente}"),
                       conversation_id=cid, agente=body.agente)
    except Exception as e:  # falla del proveedor del modelo / límite del agente: nunca como éxito
        log.exception("fallo del agente")
        raise HTTPException(502, f"El agente no pudo responder ({type(e).__name__}). Intente de nuevo.") from e


fastapi_routes.add_a2a_routes_to_fastapi(app, agent_card_routes=cards, jsonrpc_routes=rpc)


# ── interfaz web ───────────────────────────────────────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def portada():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
