"""Registro de agentes locales. Cada agente es un agente ReAct de LangChain con su prompt y sus herramientas.

Los tres comparten el mismo modelo (Claude) y un checkpointer en memoria: el `thread_id` es el contextId A2A,
así cada conversación conserva su memoria por agente.
"""
from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.costos import AVISO_TRUNCADO, VentanaMensajes, registrar_uso_turno, truncado
from app.agentes.alejandra import herramientas as h_ale, prompts as p_ale
from app.agentes.bryhan import herramientas as h_bry, prompts as p_bry
from app.agentes.carlos import herramientas as h_car, prompts as p_car

@dataclass
class AgenteDef:
    id: str                      # graph id / nombre en la Agent Card
    nombre: str                  # nombre de la persona que representa
    especialidad: str            # una línea para el selector y el enrutador
    descripcion: str             # descripción larga (Agent Card)
    skills: list[tuple]          # (id, nombre, descripción, tags, ejemplos)
    ejemplos: list[str]
    herramientas: list
    system_prompt: str
    autor: str = ""
    grafo: object = field(default=None, repr=False)


AGENTES_DEF: list[AgenteDef] = [
    AgenteDef(
        id="agente_alejandra", nombre="Alejandra Ubilla", autor="Alejandra Ubilla",
        especialidad="auditoría de cumplimiento normativo y tributario (CMF y SII)",
        descripcion=(
            "Alejandra: agente de auditoría de cumplimiento normativo y tributario (CMF y SII). Rigurosa, directa y "
            "paciente; cita la fuente o norma de cada dato. Cruza SII y CMF para empresas y revisa fondos regulados "
            "(concentración, ratios, checklists). ALCANCE: solo recomienda; no ejecuta acciones correctivas, no informa a "
            "organismos ni da asesoría tributaria personal, legal o de inversión. Pide los datos que faltan (RUT, nombre "
            "del fondo, tipo de empresa o fondo) antes de concluir. DATOS SIMULADOS para demostración."),
        skills=[
            ("estado-tributario-sii", "Estado tributario SII",
             "Consulta por RUT el estado de actividad, si las declaraciones de renta/IVA están al día, la fecha de la "
             "última declaración y las observaciones vigentes; señala atrasos. Datos simulados.",
             ["sii", "tributario", "rut"], ["¿Qué observaciones tiene en el SII el RUT 77.987.654-3?"]),
            ("cumplimiento-cruzado-cmf-sii", "Cumplimiento cruzado CMF + SII",
             "Cruza SII y CMF para determinar si una empresa cumple (declaraciones al día, sin sanciones ni observaciones) "
             "y devuelve alertas y normativa aplicable (p. ej. NCG 461). Solo recomienda el siguiente paso.",
             ["cmf", "sii", "cumplimiento"],
             ["¿Cumple con la normativa la empresa 77.987.654-3?", "¿Tiene sanciones vigentes en la CMF el RUT 78.555.111-2?"]),
            ("auditoria-fondos", "Auditoría de fondos de inversión",
             "Revisa un fondo: concentración por emisor vs. límite NCG 365 (20%), TER y rentabilidad neta estimada "
             "(rentabilidad 12m - TER), y genera el checklist según tipo (accionario, deuda, inmobiliario).",
             ["fondos", "ncg-365", "ratios"],
             ["Revisa el cumplimiento normativo del fondo FM Inmobiliario", "Calcula los ratios de FM Deuda Privada"]),
            ("checklist-empresa", "Checklist de auditoría de empresa",
             "Genera el checklist de cumplimiento administrativo y contable según el tipo de empresa: sa, spa o ltda.",
             ["checklist", "auditoria"], ["Dame el checklist de auditoría para una SpA"]),
        ],
        ejemplos=["¿Cumple con la normativa la empresa 77.987.654-3?", "Revisa la concentración del fondo FM Inmobiliario"],
        herramientas=h_ale.HERRAMIENTAS, system_prompt=p_ale.SYSTEM_PROMPT,
    ),
    AgenteDef(
        id="agente_bryhan", nombre="Bryhan Cortes", autor="Bryhan Cortes",
        especialidad="AI Engineering aplicado a procesos empresariales y financieros",
        descripcion=(
            "Agente profesional de Bryhan Cortes (no es Bryhan): AI Engineering, Data Science, automatización e IA "
            "aplicada a procesos empresariales y financieros. Analítico, estructurado, directo y pragmático; separa "
            "HECHOS documentados de INFERENCIAS y SUGERENCIAS, no inventa experiencia ni métricas y no asume que todo "
            "problema requiere IA. Puede analizar procesos, diseñar soluciones de forma conceptual y comparar alternativas "
            "técnicas; no ejecuta acciones externas ni toma decisiones finales."),
        skills=[
            ("analisis-procesos-automatizacion", "Analizar procesos y proponer automatización o IA",
             "Identifica objetivo, proceso actual, reglas, datos, decisiones y riesgos; decide si conviene automatización "
             "tradicional, datos, Machine Learning, IA generativa o procesamiento documental.",
             ["automatizacion", "ia", "procesos"], ["¿Conviene automatizar la revisión de contratos con IA?"]),
            ("diseno-soluciones-data-ia", "Diseñar soluciones de Data e IA (conceptual)",
             "Estructura componentes, responsabilidades, flujo de información, integraciones, evaluación, seguridad y "
             "mantenibilidad. Distingue una arquitectura propuesta de una solución implementada.",
             ["arquitectura", "data", "ia"], ["Propón un plan para automatizar el cierre de informes de cumplimiento"]),
            ("comparacion-alternativas-tecnicas", "Explicar, comparar y recomendar alternativas técnicas",
             "Define criterios, ventajas, desventajas y trade-offs, y entrega una recomendación condicionada al contexto.",
             ["comparacion", "tradeoffs"], ["Compara RPA vs. un agente de IA para conciliar documentos"]),
        ],
        ejemplos=["¿Conviene automatizar la revisión de contratos con IA?", "Compara RPA vs. agentes de IA para conciliaciones"],
        herramientas=h_bry.HERRAMIENTAS, system_prompt=p_bry.SYSTEM_PROMPT,
    ),
    AgenteDef(
        id="agente_carlos", nombre="Carlos Monroy", autor="Carlos Monroy",
        especialidad="ingeniería comercial y marketing (servicio público, inmobiliario y proyectos)",
        descripcion=(
            "Agente de soluciones comerciales y marketing: empático, directo y educado. Analiza necesidades, propuesta de "
            "valor y viabilidad de proyectos mediante observación y verificación documental; distingue hechos de supuestos, "
            "compara alternativas con criterios explícitos y propone pasos concretos. No inventa experiencias ni "
            "credenciales y pide autorización antes de enviar, publicar o asumir compromisos."),
        skills=[
            ("analisis-necesidades", "Analizar antecedentes e identificar necesidades",
             "Analiza información y antecedentes para identificar la necesidad real detrás de una consulta.",
             ["marketing", "necesidades"], ["¿Cómo detecto por qué mis campañas no convierten?"]),
            ("explicacion-procesos", "Explicar conceptos y procesos",
             "Explica conceptos y procesos de forma clara y ordenada, paso a paso.", ["procesos", "claridad"],
             ["Explícame cómo armar una propuesta de valor"]),
            ("comparacion-alternativas", "Comparar alternativas con criterios explícitos",
             "Compara alternativas según costos, plazos, recursos y riesgos.", ["comparacion", "viabilidad"],
             ["Compara dos estrategias de captación para un proyecto inmobiliario"]),
            ("verificacion-documental", "Verificar documentos y distinguir hechos de supuestos",
             "Revisa antecedentes documentales y separa lo verificado de lo supuesto.", ["documentos", "verificacion"],
             ["¿Qué debo verificar antes de comunicar este beneficio?"]),
            ("propuesta-soluciones", "Proponer soluciones concretas y pasos",
             "Propone soluciones concretas con pasos de implementación y métricas de evaluación.", ["soluciones", "plan"],
             ["Propón un plan para mejorar la conversión de consultas en visitas"]),
        ],
        ejemplos=["¿Cómo mejoro la conversión de consultas en visitas?", "Ayúdame con la propuesta de valor de un proyecto"],
        herramientas=h_car.HERRAMIENTAS, system_prompt=p_car.SYSTEM_PROMPT,
    ),
]

AGENTES: dict[str, AgenteDef] = {a.id: a for a in AGENTES_DEF}


def construir_agentes(checkpointer=None) -> None:
    """Crea el grafo ReAct de cada agente (no hace llamadas de red). Sin checkpointer usa memoria."""
    checkpointer = checkpointer or InMemorySaver()
    modelo = ChatAnthropic(model=config.MODELO, max_retries=2, timeout=60, max_tokens=config.MAX_TOKENS)
    # Ahorro de tokens: ventana de historial + caché del prompt de sistema (ver app/costos.py).
    middleware = [VentanaMensajes(config.HISTORIAL_MAX_MENSAJES)]
    if config.PROMPT_CACHE:
        middleware.append(AnthropicPromptCachingMiddleware(ttl="5m", unsupported_model_behavior="ignore"))
    for a in AGENTES_DEF:
        a.grafo = create_agent(model=modelo, tools=a.herramientas, system_prompt=a.system_prompt,
                               checkpointer=checkpointer, name=a.id, middleware=middleware)


def texto(msg) -> str:
    """El contenido puede venir como lista de partes; se aplana a texto."""
    c = msg.content
    return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if isinstance(p, dict))


async def responder(agente_id: str, mensaje: str, thread_id: str) -> str:
    a = AGENTES[agente_id]
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 12}  # tope de ciclo
    r = await a.grafo.ainvoke({"messages": [{"role": "user", "content": mensaje}]}, cfg)
    registrar_uso_turno(agente_id, thread_id, r["messages"])
    final = r["messages"][-1]
    return texto(final) + (AVISO_TRUNCADO if truncado(final) else "")      # nunca presentar una respuesta cortada como completa
