"""Control del consumo de tokens sin degradar lo que pide el taller (fidelidad de datos, A2A, mensajes claros).

· Ventana de mensajes: el modelo recibe solo los últimos N mensajes (la memoria completa queda guardada).
· Topes de longitud (max_tokens): una respuesta cortada se AVISA, nunca se presenta como completa.
· Caché de prompts de Anthropic: el prompt de sistema repetido en cada llamada sale más barato.
· Registro de tokens por llamada: permite ver cuánto se gasta (`tokens agente=… entrada=… salida=… cache_lectura=…`).
"""
import logging

from langchain.agents.middleware import AgentMiddleware

log = logging.getLogger("tokens")

AVISO_TRUNCADO = "\n\n> ⚠ La respuesta se cortó por el límite de longitud (MAX_TOKENS) y puede estar incompleta."


def ventana(mensajes: list, maximo: int) -> list:
    """Últimos `maximo` mensajes, empezando siempre en un mensaje de la persona: así nunca queda un resultado de
    herramienta sin su llamada (la API de Anthropic lo rechazaría). `maximo` <= 0 desactiva el recorte."""
    if maximo <= 0 or len(mensajes) <= maximo:
        return mensajes
    cola = mensajes[-maximo:]
    for i, m in enumerate(cola):
        if getattr(m, "type", "") == "human":
            return cola[i:]
    for j in range(len(mensajes) - 1, -1, -1):          # ventana sin mensaje humano: desde el último que hubo
        if getattr(mensajes[j], "type", "") == "human":
            return mensajes[j:]
    return mensajes


class VentanaMensajes(AgentMiddleware):
    """Recorta lo que se ENVÍA al modelo; el historial guardado (checkpointer) no se toca."""

    def __init__(self, maximo: int):
        super().__init__()
        self.maximo = maximo

    def wrap_model_call(self, request, handler):
        return handler(request.override(messages=ventana(request.messages, self.maximo)))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(messages=ventana(request.messages, self.maximo)))


def truncado(msg) -> bool:
    return (getattr(msg, "response_metadata", None) or {}).get("stop_reason") == "max_tokens"


def _uso(um: dict | None) -> tuple[int, int, int, int]:
    um = um or {}
    d = um.get("input_token_details") or {}
    return um.get("input_tokens", 0), um.get("output_tokens", 0), d.get("cache_read", 0), d.get("cache_creation", 0)


def registrar_uso_turno(nombre: str, conv: str, mensajes: list) -> dict:
    """Suma los tokens de las llamadas al modelo del último turno (desde el último mensaje de la persona)."""
    ent = sal = lec = esc = n = 0
    for m in reversed(mensajes):
        if getattr(m, "type", "") == "human":
            break
        if getattr(m, "usage_metadata", None):
            e, s, l, c = _uso(m.usage_metadata)
            ent, sal, lec, esc, n = ent + e, sal + s, lec + l, esc + c, n + 1
    log.info("tokens agente=%s conv=%s llamadas=%d entrada=%d salida=%d cache_lectura=%d cache_escritura=%d",
             nombre, conv[:16], n, ent, sal, lec, esc)
    return {"llamadas": n, "entrada": ent, "salida": sal, "cache_lectura": lec, "cache_escritura": esc}


def registrar_uso_llamada(nombre: str, msg) -> None:
    e, s, l, c = _uso(getattr(msg, "usage_metadata", None))
    log.info("tokens aux=%s llamadas=1 entrada=%d salida=%d cache_lectura=%d cache_escritura=%d", nombre, e, s, l, c)
