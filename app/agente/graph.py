"""Agente auditor (Alejandra): agente ReAct de LangChain con memoria por conversación (thread_id)."""
from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.agente.herramientas import HERRAMIENTAS
from app.agente.a2a_par import herramientas_par
from app.agente.prompts import SYSTEM_PROMPT

# La memoria vive en el proceso (InMemorySaver): se pierde al reiniciar. Ver README -> "Datos que se conservan".
checkpointer = InMemorySaver()


def construir_agente():
    modelo = ChatAnthropic(model=config.MODELO, max_retries=2, timeout=60)
    return create_agent(
        model=modelo,
        tools=HERRAMIENTAS + herramientas_par(),
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        name=config.AGENT_ID,
    )


def texto(msg) -> str:
    """El contenido puede venir como lista de partes; se aplana a texto."""
    c = msg.content
    return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if isinstance(p, dict))


async def responder(agente, mensaje: str, conversation_id: str) -> str:
    cfg = {"configurable": {"thread_id": conversation_id}, "recursion_limit": 12}  # tope de ciclo
    r = await agente.ainvoke({"messages": [{"role": "user", "content": mensaje}]}, cfg)
    return texto(r["messages"][-1])
