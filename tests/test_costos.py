"""Ahorro de tokens que NO degrada lo que pide el taller: la ventana de historial no deja mensajes huérfanos, una respuesta
cortada se avisa, y el modo económico solo cambia modelos y topes. Sin red ni modelo."""
import asyncio
import importlib

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import config, costos
from app import orquestador as orq
from app.agentes import registro


def turno(i: int, con_herramienta=True):
    """Un turno completo: persona -> IA (llama herramienta) -> resultado -> IA (responde)."""
    base = [HumanMessage(content=f"pregunta {i}", id=f"h{i}")]
    if con_herramienta:
        base += [AIMessage(content="", id=f"a{i}", tool_calls=[{"name": "t", "args": {}, "id": f"c{i}"}]),
                 ToolMessage(content="dato", tool_call_id=f"c{i}", id=f"t{i}")]
    return base + [AIMessage(content=f"respuesta {i}", id=f"r{i}")]


def historial(n_turnos):
    return [m for i in range(n_turnos) for m in turno(i)]


# ── ventana de mensajes ──
def test_ventana_corta_y_empieza_en_un_mensaje_de_la_persona():
    msgs = historial(10)                                    # 40 mensajes
    v = costos.ventana(msgs, 10)
    assert len(v) <= 10 and v[0].type == "human"
    assert v[-1] is msgs[-1]                                # lo más reciente se conserva


def test_ventana_nunca_deja_un_resultado_de_herramienta_sin_su_llamada():
    msgs = historial(6)
    for maximo in range(1, 25):
        v = costos.ventana(msgs, maximo)
        ids_llamadas = {tc["id"] for m in v if isinstance(m, AIMessage) for tc in m.tool_calls}
        assert all(m.tool_call_id in ids_llamadas for m in v if isinstance(m, ToolMessage)), maximo


def test_ventana_sin_recorte_cuando_cabe_o_esta_desactivada():
    msgs = historial(2)
    assert costos.ventana(msgs, 50) is msgs and costos.ventana(msgs, 0) is msgs


def test_ventana_sin_humano_en_la_cola_retrocede_al_ultimo_humano():
    msgs = [HumanMessage(content="x", id="h")] + [AIMessage(content=str(i), id=f"a{i}") for i in range(8)]
    v = costos.ventana(msgs, 3)
    assert v[0].type == "human" and len(v) == 9              # prefiere conservar la consistencia a recortar de más


def test_el_middleware_recorta_lo_que_se_envia_y_no_el_historial():
    class Req:
        def __init__(self, messages):
            self.messages = messages

        def override(self, **kw):
            return Req(kw["messages"])
    vistos = []
    original = historial(8)
    mw = costos.VentanaMensajes(8)
    mw.wrap_model_call(Req(original), lambda r: vistos.append(r.messages))
    asyncio.run(mw.awrap_model_call(Req(original), lambda r: _async_visto(vistos, r)))
    assert all(len(v) <= 8 and v[0].type == "human" for v in vistos) and len(vistos) == 2
    assert len(original) == 32                               # el historial guardado no se modifica


async def _async_visto(vistos, r):
    vistos.append(r.messages)


# ── respuestas cortadas se avisan ──
def test_uso_de_tokens_suma_solo_el_ultimo_turno():
    um = lambda e, s: {"input_tokens": e, "output_tokens": s, "total_tokens": e + s}          # noqa: E731
    msgs = [HumanMessage(content="viejo"), AIMessage(content="a", usage_metadata=um(100, 10)), HumanMessage(content="nuevo"),
            AIMessage(content="", usage_metadata=um(200, 20), tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
            ToolMessage(content="d", tool_call_id="1"), AIMessage(content="fin", usage_metadata=um(300, 30))]
    r = costos.registrar_uso_turno("agente_x", "conv", msgs)
    assert r == {"llamadas": 2, "entrada": 500, "salida": 50, "cache_lectura": 0, "cache_escritura": 0}


def test_una_respuesta_truncada_se_avisa_en_los_especialistas(monkeypatch):
    class Grafo:
        async def ainvoke(self, entrada, cfg):
            return {"messages": [HumanMessage(content="q"),
                                 AIMessage(content="La concentración es 35% y el límite", response_metadata={"stop_reason": "max_tokens"})]}
    monkeypatch.setattr(registro.AGENTES["agente_alejandra"], "grafo", Grafo())
    t = asyncio.run(registro.responder("agente_alejandra", "q", "c1"))
    assert t.startswith("La concentración es 35%") and "se cortó por el límite" in t


def test_una_respuesta_completa_no_lleva_aviso(monkeypatch):
    class Grafo:
        async def ainvoke(self, entrada, cfg):
            return {"messages": [HumanMessage(content="q"), AIMessage(content="ok", response_metadata={"stop_reason": "end_turn"})]}
    monkeypatch.setattr(registro.AGENTES["agente_alejandra"], "grafo", Grafo())
    assert asyncio.run(registro.responder("agente_alejandra", "q", "c1")) == "ok"


def test_el_coordinador_tambien_avisa_si_se_corta(monkeypatch):
    class Modelo:
        async def ainvoke(self, mensajes):
            return AIMessage(content="Hola, soy el Coordinador", response_metadata={"stop_reason": "max_tokens"})
    monkeypatch.setattr(orq, "modelo_aux", lambda: Modelo())
    t = asyncio.run(orq.coordinador("hola", "c", orq.roster()))
    assert "Coordinador" in t and "se cortó por el límite" in t


# ── configuración de modelos y modo económico ──
def _recargar(monkeypatch, **env):
    for k in ("ECONOMICO", "MODELO", "MODELO_ROUTER", "MAX_TOKENS", "MAX_TOKENS_AUX", "HISTORIAL_MAX_MENSAJES", "PROMPT_CACHE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


def test_por_defecto_especialistas_con_sonnet_y_tareas_livianas_con_haiku(monkeypatch):
    try:
        c = _recargar(monkeypatch)
        assert c.MODELO == "claude-sonnet-5-5" and c.MODELO_ROUTER == "claude-haiku-5-5"
        assert c.MAX_TOKENS >= 2000 and c.PROMPT_CACHE is True and c.HISTORIAL_MAX_MENSAJES >= 20
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_modo_economico_usa_haiku_en_todo_y_topes_menores(monkeypatch):
    try:
        c = _recargar(monkeypatch, ECONOMICO="1", MODELO="claude-sonnet-5-5")     # ECONOMICO manda sobre MODELO
        assert c.MODELO == "claude-haiku-5-5" and c.MODELO_ROUTER == "claude-haiku-5-5"
        assert c.MAX_TOKENS < 2500 and c.HISTORIAL_MAX_MENSAJES < 24
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_los_valores_vacios_del_env_no_rompen_la_configuracion(monkeypatch):
    try:
        c = _recargar(monkeypatch, MODELO_ROUTER="", MAX_TOKENS="", HISTORIAL_MAX_MENSAJES="", PROMPT_CACHE="")
        assert c.MODELO_ROUTER == "claude-haiku-5-5" and c.MAX_TOKENS == 2500 and c.PROMPT_CACHE is True
    finally:
        monkeypatch.undo()
        importlib.reload(config)
