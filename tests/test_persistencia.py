"""Persistencia del historial y de la memoria de los agentes (sin modelo). Postgres se prueba con un servidor
embebido (paquete `pgserver`, solo desarrollo); si no está instalado esa prueba se omite."""
import asyncio

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from app.almacen import Almacen

RES = {"solicitud_id": "rid1", "conversation_id": "conv-1", "modo": "paralelo", "automatico": True,
       "motivo_ruta": "prueba", "respuesta": "## Conclusión\nFondo con 35% vs 20%", "conclusion": "Conclusión",
       "agentes": [{"id": "agente_alejandra", "nombre": "Alejandra", "estado": "ok", "ms": 1200, "context_id": "c",
                    "respuesta": "35% vs 20%"}], "verificacion": {"cifras_no_respaldadas": [], "sintesis_generada": True},
       "ms_total": 1500}


async def _ciclo(entorno: dict, monkeypatch):
    """Guarda una consulta y un checkpoint, cierra todo, reabre y comprueba que sigue ahí."""
    for k, v in entorno.items():
        monkeypatch.setenv(k, v)
    a = Almacen()
    await a.abrir()
    backend = a.backend
    await a.guardar_consulta(RES, "¿Cumple el fondo FM Inmobiliario?")
    cfg = {"configurable": {"thread_id": "hilo-1", "checkpoint_ns": ""}}
    cp = empty_checkpoint()
    cp["channel_values"] = {"mensaje": "recuerdame: 35%"}
    await a.checkpointer.aput(cfg, cp, {"source": "input", "step": 0}, {})
    await a.cerrar()

    b = Almacen()  # "reinicio" del servicio
    await b.abrir()
    try:
        lista = await b.listar()
        detalle = await b.obtener("rid1")
        conv = await b.listar("conv-1", detalle=True)
        tupla = await b.checkpointer.aget_tuple(cfg)
    finally:
        await b.cerrar()
    return backend, lista, detalle, conv, tupla


def _verificar(res):
    backend, lista, detalle, conv, tupla = res
    assert lista and lista[0]["solicitud_id"] == "rid1" and lista[0]["agentes"] == ["agente_alejandra"]
    assert detalle["respuesta"].startswith("## Conclusión") and detalle["verificacion"]["sintesis_generada"] is True
    assert detalle["agentes"][0]["ms"] == 1200 and detalle["automatico"] is True
    assert [c["solicitud_id"] for c in conv] == ["rid1"]
    assert tupla is not None and tupla.checkpoint["channel_values"]["mensaje"] == "recuerdame: 35%"  # memoria del agente


def test_sqlite_sobrevive_a_reinicios(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    res = asyncio.run(_ciclo({"DATA_DIR": str(tmp_path), "PERSISTENCIA": "auto"}, monkeypatch))
    assert res[0] == "sqlite"
    _verificar(res)


def test_postgres_sobrevive_a_reinicios(tmp_path, monkeypatch):
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(tmp_path / "pg")
    try:
        res = asyncio.run(_ciclo({"DATABASE_URL": srv.get_uri().replace("postgresql://", "postgres://")}, monkeypatch),
                          loop_factory=asyncio.SelectorEventLoop)  # psycopg async necesita este bucle en Windows
        assert res[0] == "postgres"  # (Heroku entrega el esquema antiguo postgres://; se normaliza al abrir)
        _verificar(res)
    finally:
        srv.cleanup()


def test_memoria_no_persiste_pero_funciona(monkeypatch):
    monkeypatch.setenv("PERSISTENCIA", "memoria")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    async def go():
        a = Almacen()
        await a.abrir()
        await a.guardar_consulta(RES, "q")
        return a.backend, await a.listar(), await a.obtener("rid1"), await a.obtener("no-existe")
    backend, lista, det, falta = asyncio.run(go())
    assert backend == "memoria" and lista[0]["solicitud_id"] == "rid1" and det["respuesta"] and falta is None


def test_base_inaccesible_cae_a_memoria_y_lo_informa(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nadie:x@127.0.0.1:9/nada?connect_timeout=2")

    async def go():
        a = Almacen()
        await a.abrir()
        return a.backend
    assert asyncio.run(go(), loop_factory=asyncio.SelectorEventLoop) == "memoria"
