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
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill, HTTPAuthSecurityScheme,
                       APIKeySecurityScheme, SecurityRequirement, SecurityScheme)

from app import config
from app.agente.graph import responder
from app.agente.prompts import ESPECIALIDAD, NOMBRE

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


SKILLS_CARD = [
    ("estado-tributario-sii", "Estado tributario SII",
     "Consulta por RUT el estado de actividad, si las declaraciones de renta/IVA están al día, la fecha de la última "
     "declaración y las observaciones vigentes; señala atrasos. Datos simulados.",
     ["sii", "tributario", "rut"],
     ["¿Qué observaciones tiene en el SII el RUT 77.987.654-3?"]),
    ("cumplimiento-cruzado-cmf-sii", "Cumplimiento cruzado CMF + SII",
     "Cruza SII y CMF para determinar si una empresa cumple (declaraciones al día, sin sanciones ni observaciones) y "
     "devuelve alertas y normativa aplicable (p. ej. NCG 461). Solo recomienda el siguiente paso; no ejecuta acciones.",
     ["cmf", "sii", "cumplimiento"],
     ["¿Cumple con la normativa la empresa 77.987.654-3?", "¿Tiene sanciones vigentes en la CMF el RUT 78.555.111-2?"]),
    ("auditoria-fondos", "Auditoría de fondos de inversión",
     "Revisa un fondo: concentración por emisor vs. límite NCG 365 (20%), TER y rentabilidad neta estimada "
     "(rentabilidad 12m - TER), y genera el checklist de auditoría según tipo (accionario, deuda, inmobiliario).",
     ["fondos", "ncg-365", "ratios"],
     ["Revisa el cumplimiento normativo del fondo FM Inmobiliario", "Calcula los ratios de FM Deuda Privada"]),
    ("checklist-empresa", "Checklist de auditoría de empresa",
     "Genera el checklist de cumplimiento administrativo y contable según el tipo de empresa: sa, spa o ltda.",
     ["checklist", "auditoria"],
     ["Dame el checklist de auditoría para una SpA"]),
]


def construir_card() -> AgentCard:
    return AgentCard(
        name=config.AGENT_ID,
        description=(
            f"{NOMBRE}: agente de {ESPECIALIDAD}. Rigurosa, directa y paciente; cita la fuente o norma de cada dato. "
            "Cruza SII y CMF para empresas y revisa fondos regulados (concentración, ratios, checklists). "
            "ALCANCE: solo recomienda; no ejecuta acciones correctivas, no informa a organismos ni da asesoría "
            "tributaria personal, legal o de inversión. Pide los datos que faltan (RUT, nombre del fondo, tipo de "
            "empresa o fondo) antes de concluir. DATOS SIMULADOS para demostración. "
            "Acceso: enviar la clave en la cabecera X-API-Key. Envíe texto en español; use un contextId fijo por "
            "conversación para que recuerde el contexto."),
        version="1.0.0",
        documentation_url=f"{config.PUBLIC_URL}/docs",
        provider=AgentProvider(organization="Alejandra Ubilla · Taller agente auditora", url=config.PUBLIC_URL),
        supported_interfaces=[AgentInterface(url=f"{config.PUBLIC_URL}{RPC_PATH}", protocol_binding="JSONRPC",
                                             protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain"], default_output_modes=["text/plain"],
        skills=[AgentSkill(id=i, name=n, description=d, tags=t, examples=e) for i, n, d, t, e in SKILLS_CARD],
        security_schemes={"apiKey": SecurityScheme(api_key_security_scheme=APIKeySecurityScheme(
            location="header", name="X-API-Key",
            description="Clave de acceso configurada en el servidor (API_KEYS). Se solicita al administrador del servicio."))},
        security_requirements=[SecurityRequirement(schemes={"apiKey": {}})],
    )


def rutas_a2a(agente):
    card = construir_card()
    handler = DefaultRequestHandler(agent_executor=AgenteExecutor(agente), task_store=InMemoryTaskStore(), agent_card=card)
    return (create_agent_card_routes(card),
            create_jsonrpc_routes(handler, rpc_url=RPC_PATH, enable_v0_3_compat=True))
