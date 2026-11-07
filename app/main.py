"""API FastAPI del agente Alejandra (auditoría CMF/SII). Swagger en /docs.

  GET  /health   estado del servicio (público, sin datos sensibles)
  POST /chat     consulta directa al agente (protegido con X-API-Key)
  A2A            /.well-known/agent-card.json y POST /a2a (ver app/a2a_server.py)
"""
import logging
import re
from contextlib import asynccontextmanager

from a2a.server.routes import fastapi_routes
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import config
from app.a2a_server import RPC_PATH, rutas_a2a
from app.agente.graph import construir_agente, responder
from app.security import clave_valida, exigir_clave, extraer_clave

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


class ChatIn(BaseModel):
    mensaje: str = Field(
        ..., min_length=1, max_length=4000,
        description="Consulta de auditoría en lenguaje natural (máx. 4000 caracteres). Incluya el dato que necesita el "
                    "agente: RUT de la empresa (12.345.678-9), nombre del fondo o tipo de empresa/fondo. Si falta, el "
                    "agente lo pedirá en su respuesta.",
        examples=["¿Cumple con la normativa la empresa 77.987.654-3?"])
    conversation_id: str | None = Field(
        None, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$",
        description="Identificador de la conversación. Reutilícelo en las llamadas siguientes para que el agente "
                    "recuerde el contexto. Si se omite, se crea una conversación nueva y se devuelve su id.",
        examples=["auditoria-andina-001"])


class ChatOut(BaseModel):
    respuesta: str = Field(..., description="Respuesta del agente. Las cifras provienen de sus herramientas (datos "
                                            "simulados) y las recomendaciones se distinguen de los hallazgos.")
    conversation_id: str = Field(..., description="Id de la conversación; envíelo de vuelta para continuarla.")


class HealthOut(BaseModel):
    status: str = Field(..., description="'ok' si el servicio está en línea.", examples=["ok"])
    agente: str = Field(..., description="Identificador del agente expuesto.", examples=["agente_alejandra"])
    acceso_configurado: bool = Field(..., description="False si el servidor no tiene API_KEYS (rechazará todo con 503).")


class ErrorOut(BaseModel):
    detail: str = Field(..., description="Motivo del error.")


DESCRIPCION = """
## Agente Alejandra · Auditoría de cumplimiento normativo y tributario (CMF y SII)

Servicio que expone a un agente de IA auditor. Cruza datos del **SII** y la **CMF** para evaluar si una empresa
cumple la normativa, y revisa **fondos de inversión** (concentración por emisor NCG 365, ratios, checklist).

### Qué hace y qué no
* **Hace:** consulta estado tributario (SII), fiscalización y sanciones (CMF), cumplimiento contable cruzado,
  ratios de fondos y checklists de auditoría según tipo de empresa (`sa`, `spa`, `ltda`) o de fondo
  (`accionario`, `deuda`, `inmobiliario`).
* **No hace:** no ejecuta acciones correctivas, no informa a la CMF/SII, no presenta declaraciones ni da
  asesoría tributaria personal, legal o de inversión. **Solo recomienda el siguiente paso.**
* **Datos simulados** para demostración. RUT de prueba: `76.123.456-7`, `77.987.654-3`, `78.555.111-2`.
  Fondos: `FM Acciones Chile`, `FM Deuda Privada`, `FM Inmobiliario`.

### Cómo autenticarse
Los endpoints protegidos exigen una clave configurada en el servidor (`API_KEYS`). Pulse **Authorize** y escriba
la clave, o envíe la cabecera `X-API-Key: <clave>` (también se acepta `Authorization: Bearer <clave>`).
Sin clave → `401`. Si el servidor no tiene claves configuradas → `503` (falla cerrado).

### Dos formas de usarlo
1. **API directa** (`POST /chat`): para personas y aplicaciones.
2. **Protocolo A2A** (`/.well-known/agent-card.json` + `POST /a2a`): para que **otros agentes** descubran a Alejandra
   y colaboren con ella. El `contextId` del mensaje A2A es la memoria de la conversación.

### Códigos de error
`401` clave ausente/inválida · `422` entrada inválida · `502` falla del proveedor del modelo · `503` acceso no configurado.
"""

TAGS = [
    {"name": "Estado", "description": "Comprobación de que el servicio está en línea. Público, sin datos sensibles."},
    {"name": "Agente", "description": "Consulta directa al agente auditor. **Requiere clave.** Mantiene memoria por `conversation_id`."},
    {"name": "A2A: Agent Card", "description": "Tarjeta pública del agente (nombre, habilidades, URL y esquema de seguridad) para que otros agentes A2A lo descubran."},
    {"name": "A2A: JSON-RPC", "description": "Endpoint del protocolo A2A (`message/send`). **Requiere clave** (`X-API-Key`). El `contextId` identifica la conversación."},
]

app = FastAPI(
    title="Agente Alejandra · Auditoría CMF/SII",
    version="1.0.0",
    summary="Agente auditor de cumplimiento normativo y tributario con API protegida y protocolo A2A",
    description=DESCRIPCION,
    openapi_tags=TAGS,
    contact={"name": "Alejandra Ubilla"},
)
agente = construir_agente()
cards, rpc = rutas_a2a(agente)


@app.middleware("http")
async def proteger_a2a(request: Request, call_next):
    """El endpoint JSON-RPC A2A exige la misma clave que /chat (la Agent Card queda pública)."""
    if request.url.path.rstrip("/") == RPC_PATH:
        if not config.API_KEYS:
            return JSONResponse({"detail": "Acceso deshabilitado: API_KEYS no configuradas."}, status_code=503)
        if not clave_valida(extraer_clave(request)):
            log.warning("A2A rechazado: clave ausente o inválida")
            return JSONResponse({"detail": "Clave de acceso ausente o inválida."}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


@app.get("/health", tags=["Estado"], response_model=HealthOut, summary="Estado del servicio",
         description="Indica si el servicio está en línea y si tiene claves de acceso configuradas. No requiere clave "
                     "y no expone datos sensibles.")
async def health():
    return {"status": "ok", "agente": config.AGENT_ID, "acceso_configurado": bool(config.API_KEYS)}


@app.post("/chat", response_model=ChatOut, tags=["Agente"], dependencies=[Depends(exigir_clave)],
          summary="Consultar al agente auditor",
          description="Envía una consulta al agente Alejandra, que decide qué herramientas usar (SII, CMF, fondos, "
                      "checklists, apuntes), cruza los datos y responde en español citando la fuente. Si falta un dato "
                      "(RUT, nombre del fondo, tipo de empresa/fondo) lo pregunta en vez de inventarlo.\n\n"
                      "Para continuar la conversación, reenvíe el `conversation_id` recibido. "
                      "Si el agente par A2A está configurado y no responde, el agente lo informa y no inventa su aporte.",
          responses={401: {"model": ErrorOut, "description": "Clave ausente o inválida."},
                     422: {"description": "Entrada inválida (mensaje vacío, >4000 caracteres o id con caracteres no permitidos)."},
                     502: {"model": ErrorOut, "description": "Falló el proveedor del modelo; la consulta NO se realizó."},
                     503: {"model": ErrorOut, "description": "El servidor no tiene API_KEYS configuradas."}})
async def chat(body: ChatIn):
    cid = body.conversation_id or re.sub(r"\W", "", __import__("uuid").uuid4().hex)
    log.info("chat conversation=%s chars=%d", cid, len(body.mensaje))
    try:
        return ChatOut(respuesta=await responder(agente, body.mensaje, cid), conversation_id=cid)
    except Exception as e:  # falla del proveedor del modelo / límite del agente: nunca como éxito
        log.exception("fallo del agente")
        raise HTTPException(502, f"El agente no pudo responder ({type(e).__name__}). Intente de nuevo.") from e


fastapi_routes.add_a2a_routes_to_fastapi(app, agent_card_routes=cards, jsonrpc_routes=rpc)
