"""Cliente A2A (SDK oficial): descubre la Agent Card de un agente y le envía un mensaje con message/send."""
import uuid

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.helpers.proto_helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

from app import config


class ErrorA2A(RuntimeError):
    """Falla de comunicación o de ejecución en el agente remoto (nunca se presenta como éxito)."""


def _extraer_texto(evento) -> tuple[str, str | None]:
    """Devuelve (texto, error). error != None si la tarea remota no terminó bien."""
    if evento.HasField("message"):
        return get_message_text(evento.message), None
    if evento.HasField("task"):
        t = evento.task
        if t.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            return get_message_text(t.status.message), None  # el agente pide datos: se transmite tal cual
        if t.status.state != TaskState.TASK_STATE_COMPLETED:
            return "", f"El agente remoto terminó en estado {TaskState.Name(t.status.state)}."
        partes = [get_artifact_text(a) for a in t.artifacts] or [get_message_text(t.status.message)]
        return "\n".join(p for p in partes if p), None
    return "", None


async def enviar_a2a(card_url: str, texto: str, context_id: str, api_key: str = "", request_id: str = "",
                     url_rpc: str | None = None, timeout: float | None = None) -> str:
    """Envía `texto` al agente cuya Agent Card está en `card_url`. `url_rpc` reemplaza la URL del endpoint de la
    tarjeta (se usa para llamadas internas sin salir a internet). Devuelve el texto de la respuesta."""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    if request_id:
        headers["X-Request-ID"] = request_id
    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout or config.A2A_TIMEOUT) as http:
            r = await http.get(card_url)
            r.raise_for_status()
            card = parse_agent_card(r.json())
            if url_rpc:
                for i in card.supported_interfaces:
                    i.url = url_rpc
            cliente = ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create(card)
            msg = new_text_message(texto, role=Role.ROLE_USER, context_id=context_id)
            msg.message_id = uuid.uuid4().hex
            textos = []
            async for evento in cliente.send_message(SendMessageRequest(message=msg)):
                t, error = _extraer_texto(evento)
                if error:
                    raise ErrorA2A(error)
                if t:
                    textos.append(t)
    except ErrorA2A:
        raise
    except httpx.HTTPStatusError as e:
        raise ErrorA2A(f"El agente remoto respondió HTTP {e.response.status_code}.") from e
    except httpx.TimeoutException as e:
        raise ErrorA2A("El agente remoto no respondió a tiempo.") from e
    except Exception as e:
        raise ErrorA2A(f"No se pudo consultar al agente remoto ({type(e).__name__}).") from e
    if not textos:
        raise ErrorA2A("El agente remoto no devolvió contenido.")
    return "\n".join(textos)
