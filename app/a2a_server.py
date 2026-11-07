"""Servidor A2A (SDK oficial a2a-sdk) montado dentro de FastAPI.

  GET  /.well-known/agent-card.json   Agent Card (pública: describe al agente y cómo autenticarse)
  POST /a2a                           JSON-RPC (SendMessage / message/send, GetTask, ...) -> protegido con la clave
"""
import logging

from a2a.helpers.proto_helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentSkill, HTTPAuthSecurityScheme,
                       APIKeySecurityScheme, SecurityRequirement, SecurityScheme)

from app import config
from app.agente.graph import responder
from app.agente.prompts import ESPECIALIDAD, NOMBRE, SKILLS

log = logging.getLogger("a2a")
RPC_PATH = "/a2a"


class AgenteExecutor(AgentExecutor):
    def __init__(self, agente):
        self.agente = agente

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        pregunta = context.get_user_input().strip()
        cid = context.context_id or context.task_id or "a2a-sin-contexto"
        if not pregunta:
            await event_queue.enqueue_event(new_text_message(
                "No recibí texto en el mensaje. Envía una consulta de auditoría.", context_id=cid))
            return
        log.info("A2A message context=%s chars=%d", cid, len(pregunta))
        try:
            texto = await responder(self.agente, pregunta, cid)
        except Exception as e:  # falla del proveedor: el SDK la convierte en error JSON-RPC, no en éxito
            log.exception("fallo del agente")
            raise RuntimeError(f"El agente no pudo responder ({type(e).__name__}).") from e
        await event_queue.enqueue_event(new_text_message(texto, context_id=cid))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Este agente responde de forma inmediata; no hay tareas que cancelar.")


def construir_card() -> AgentCard:
    return AgentCard(
        name=config.AGENT_ID,
        description=f"{NOMBRE}: {ESPECIALIDAD}. Datos simulados; solo recomienda, no ejecuta acciones.",
        version="1.0.0",
        supported_interfaces=[AgentInterface(url=f"{config.PUBLIC_URL}{RPC_PATH}", protocol_binding="JSONRPC",
                                             protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        skills=[AgentSkill(id=f"skill-{i}", name=s, description=s, tags=["auditoria", "cmf", "sii"])
                for i, s in enumerate(SKILLS, 1)],
        security_schemes={"apiKey": SecurityScheme(api_key_security_scheme=APIKeySecurityScheme(
            location="header", name="X-API-Key", description="Clave de acceso del servicio"))},
        security_requirements=[SecurityRequirement(schemes={"apiKey": {}})],
    )


def rutas_a2a(agente):
    card = construir_card()
    handler = DefaultRequestHandler(agent_executor=AgenteExecutor(agente), task_store=InMemoryTaskStore(), agent_card=card)
    return (create_agent_card_routes(card),
            create_jsonrpc_routes(handler, rpc_url=RPC_PATH, enable_v0_3_compat=True))
