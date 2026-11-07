"""Cliente A2A: Alejandra consulta a un agente par (p. ej. Benjamín) por el protocolo A2A.

Es opcional: solo se registra la herramienta si A2A_PEER_CARD_URL está definida. Lo que responde el
par se devuelve al modelo entre etiquetas <respuesta_agente_remoto> (es un dato, no una instrucción)
y el texto se entrega sin modificar para conservar cifras, unidades y supuestos.
"""
import uuid

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.helpers.proto_helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app import config as ajustes

# Cada conversación de Alejandra reutiliza un contextId propio con el par (para que el par recuerde).
_contextos: dict[str, str] = {}


def _extraer_texto(evento) -> tuple[str, str | None]:
    """Devuelve (texto, error). error != None si la tarea remota no terminó bien."""
    if evento.HasField("message"):
        return get_message_text(evento.message), None
    if evento.HasField("task"):
        t = evento.task
        if t.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            return get_message_text(t.status.message), None  # el par pide datos: se lo transmitimos al usuario
        if t.status.state != TaskState.TASK_STATE_COMPLETED:
            return "", f"El agente remoto terminó en estado {TaskState.Name(t.status.state)}."
        partes = [get_artifact_text(a) for a in t.artifacts] or [get_message_text(t.status.message)]
        return "\n".join(p for p in partes if p), None
    return "", None


async def consultar_par(pregunta: str, conversation_id: str) -> str:
    headers = {"X-API-Key": ajustes.A2A_PEER_API_KEY} if ajustes.A2A_PEER_API_KEY else {}
    async with httpx.AsyncClient(headers=headers, timeout=ajustes.A2A_PEER_TIMEOUT) as http:
        r = await http.get(ajustes.A2A_PEER_CARD_URL)
        r.raise_for_status()
        card = parse_agent_card(r.json())
        cliente = ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create(card)
        msg = new_text_message(pregunta, role=Role.ROLE_USER,
                               context_id=_contextos.setdefault(conversation_id, str(uuid.uuid4())))
        msg.message_id = uuid.uuid4().hex
        textos = []
        async for evento in cliente.send_message(SendMessageRequest(message=msg)):
            texto, error = _extraer_texto(evento)
            if error:
                raise RuntimeError(error)
            if texto:
                textos.append(texto)
    if not textos:
        raise RuntimeError("El agente remoto no devolvió contenido.")
    return "\n".join(textos)


def herramientas_par() -> list:
    if not ajustes.A2A_PEER_CARD_URL:
        return []

    @tool(f"consultar_{ajustes.A2A_PEER_NAME}")
    async def consultar_agente_par(pregunta: str, config: RunnableConfig) -> str:
        """Consulta por A2A a un agente especialista par (control interno y riesgos operacionales).
        Úsalo para lo que no es de tu área (ej. diseño de controles internos, matriz de riesgo).
        Escribe la pregunta completa con todo el contexto necesario. Devuelve su respuesta tal cual."""
        cid = config.get("configurable", {}).get("thread_id", "sin-conversacion")
        try:
            texto = await consultar_par(pregunta, cid)
        except Exception as e:  # falla del par: se informa como error, nunca como éxito
            return f"ERROR: no se pudo consultar al agente remoto ({type(e).__name__}: {e}). No inventes su respuesta."
        return f"<respuesta_agente_remoto>\n{texto}\n</respuesta_agente_remoto>"

    return [consultar_agente_par]
