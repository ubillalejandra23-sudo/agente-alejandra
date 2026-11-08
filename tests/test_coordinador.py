"""El Coordinador: lo general (saludos, «¿qué puedes hacer?», fuera de alcance, consultas vagas) lo atiende una identidad
común y NO un especialista elegido al azar. Pruebas con enrutador y modelos simulados (sin red ni tokens)."""
import asyncio

import pytest

from app import orquestador as orq


class _Estructurado:
    def __init__(self, padre):
        self.padre = padre

    async def ainvoke(self, mensajes):
        if self.padre.falla_enrutador:
            raise RuntimeError("proveedor caído")
        return self.padre.decision


class FalsoModelo:
    """Imita ChatAnthropic: with_structured_output(...).ainvoke -> decisión del enrutador; ainvoke -> texto."""

    def __init__(self, decision=None, falla_enrutador=False, falla_texto=False, texto="Hola, soy el Coordinador de la red."):
        self.decision, self.falla_enrutador, self.falla_texto, self.texto = decision, falla_enrutador, falla_texto, texto
        self.sistemas: list[str] = []

    def with_structured_output(self, _esquema):
        return _Estructurado(self)

    async def ainvoke(self, mensajes):
        self.sistemas.append(mensajes[0][1])
        if self.falla_texto:
            raise RuntimeError("proveedor caído")
        return type("R", (), {"content": self.texto})()


@pytest.fixture
def guardadas(monkeypatch):
    registro = []

    async def guardar(resultado, pregunta):
        registro.append((resultado, pregunta))
    monkeypatch.setattr(orq.almacen, "guardar_consulta", guardar)
    return registro


def run(pregunta, agentes=None):
    return asyncio.run(orq.consultar(pregunta, agentes or [], "paralelo", None))


def test_un_saludo_lo_responde_el_coordinador_y_no_un_especialista(monkeypatch, guardadas):
    modelo = FalsoModelo(decision=orq.Seleccion(tipo="general", agentes=[], motivo="saludo"))
    monkeypatch.setattr(orq, "modelo_aux", lambda: modelo)

    async def prohibido(*a, **k):
        raise AssertionError("un saludo no debe consultar a ningún especialista")
    monkeypatch.setattr(orq, "_llamar", prohibido)

    async def go():
        return [ev async for ev in orq.stream("hola", [], "paralelo", None)]
    eventos = asyncio.run(go())
    enrutado = next(e for e in eventos if e["evento"] == "enrutado")
    final = eventos[-1]
    assert enrutado["general"] is True and enrutado["agentes"] == []
    assert final["evento"] == "final" and final["coordinador"] is True and final["agentes"] == []
    assert "Coordinador" in final["respuesta"] and final["conclusion"] is None
    assert guardadas and guardadas[0][1] == "hola"                      # queda en el historial
    prompt = modelo.sistemas[-1]                                          # el prompt del Coordinador
    assert all(n in prompt for n in ("Alejandra", "Bryhan", "Carlos")) and "NO eres ninguno de los" in prompt


def test_el_enrutador_no_elige_un_especialista_al_azar_si_falla(monkeypatch):
    monkeypatch.setattr(orq, "modelo_aux", lambda: FalsoModelo(falla_enrutador=True))
    ids, motivo = asyncio.run(orq.enrutar("hola", "c", orq.roster()))
    assert ids == [] and "Coordinador" in motivo                         # antes elegía el primer agente de la lista


def test_tipo_especialistas_sin_ids_validos_cae_al_coordinador(monkeypatch):
    modelo = FalsoModelo(decision=orq.Seleccion(tipo="especialistas", agentes=["agente_inexistente"], motivo="x"))
    monkeypatch.setattr(orq, "modelo_aux", lambda: modelo)
    ids, _ = asyncio.run(orq.enrutar("algo", "c", orq.roster()))
    assert ids == []


def test_tipo_general_ignora_agentes_listados(monkeypatch):
    modelo = FalsoModelo(decision=orq.Seleccion(tipo="general", agentes=["agente_bryhan"], motivo="saludo"))
    monkeypatch.setattr(orq, "modelo_aux", lambda: modelo)
    ids, _ = asyncio.run(orq.enrutar("hola", "c", orq.roster()))
    assert ids == []


def _ok(m, respuesta):
    return {"id": m.id, "nombre": m.nombre, "local": True, "context_id": "c", "estado": "ok", "ms": 5, "respuesta": respuesta}


def test_una_consulta_de_fondo_sigue_yendo_al_especialista(monkeypatch, guardadas):
    modelo = FalsoModelo(decision=orq.Seleccion(tipo="especialistas", agentes=["agente_alejandra"], motivo="auditoría"))
    monkeypatch.setattr(orq, "modelo_aux", lambda: modelo)
    llamados = []

    async def llamar(m, texto, conv, rid):
        llamados.append(m.id)
        return _ok(m, "Concentración 35%")
    monkeypatch.setattr(orq, "_llamar", llamar)
    r = run("¿Cumple el fondo FM Inmobiliario?")
    assert llamados == ["agente_alejandra"] and r["coordinador"] is False and r["respuesta"] == "Concentración 35%"


def test_si_la_persona_elige_agentes_no_interviene_el_enrutador(monkeypatch, guardadas):
    async def prohibido(*a, **k):
        raise AssertionError("el enrutador no debe usarse cuando la persona elige agentes")
    monkeypatch.setattr(orq, "enrutar", prohibido)

    async def llamar(m, texto, conv, rid):
        return _ok(m, "ok")
    monkeypatch.setattr(orq, "_llamar", llamar)
    r = run("hola", agentes=["agente_bryhan"])            # eligió a Bryhan a propósito: responde Bryhan
    assert r["coordinador"] is False and [a["id"] for a in r["agentes"]] == ["agente_bryhan"] and r["automatico"] is False


def test_si_falla_el_coordinador_se_informa_como_error(monkeypatch, guardadas):
    modelo = FalsoModelo(decision=orq.Seleccion(tipo="general", agentes=[], motivo="saludo"), falla_texto=True)
    monkeypatch.setattr(orq, "modelo_aux", lambda: modelo)
    with pytest.raises(orq.ErrorConsulta) as e:
        run("hola")
    assert "falla del proveedor" in e.value.detalle and guardadas == []
