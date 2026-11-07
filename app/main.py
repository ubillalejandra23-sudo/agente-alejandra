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
    mensaje: str = Field(..., min_length=1, max_length=4000, examples=["¿Cumple la empresa 77.987.654-3 con la normativa?"])
    conversation_id: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$",
                                        description="Identifica la conversación (memoria). Si se omite, se crea una nueva.")


class ChatOut(BaseModel):
    respuesta: str
    conversation_id: str


app = FastAPI(
    title="Agente Alejandra · Auditoría CMF/SII",
    version="1.0.0",
    description="Agente de auditoría de cumplimiento normativo y tributario (datos simulados). "
                "Use **Authorize** con su clave (X-API-Key) para probar los endpoints protegidos.",
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


@app.get("/health", tags=["Estado"])
async def health():
    return {"status": "ok", "agente": config.AGENT_ID, "acceso_configurado": bool(config.API_KEYS)}


@app.post("/chat", response_model=ChatOut, tags=["Agente"], dependencies=[Depends(exigir_clave)])
async def chat(body: ChatIn):
    cid = body.conversation_id or re.sub(r"\W", "", __import__("uuid").uuid4().hex)
    log.info("chat conversation=%s chars=%d", cid, len(body.mensaje))
    try:
        return ChatOut(respuesta=await responder(agente, body.mensaje, cid), conversation_id=cid)
    except Exception as e:  # falla del proveedor del modelo / límite del agente: nunca como éxito
        log.exception("fallo del agente")
        raise HTTPException(502, f"El agente no pudo responder ({type(e).__name__}). Intente de nuevo.") from e


fastapi_routes.add_a2a_routes_to_fastapi(app, agent_card_routes=cards, jsonrpc_routes=rpc)
