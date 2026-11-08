"""Pruebas de seguridad, validación y trazabilidad (no llaman al modelo)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, security
from app.main import app
from app.orquestador import cifras_no_respaldadas

client = TestClient(app)
OK = {"X-API-Key": "clave-de-prueba"}
IDS = ["agente_alejandra", "agente_bryhan", "agente_carlos"]


def jsonrpc(texto, ctx=None):
    return {"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {"message": {
        "role": "user", "messageId": uuid.uuid4().hex, "contextId": ctx or str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": texto}]}}}


# ── público ──
def test_health_publico():
    j = client.get("/health").json()
    assert j["status"] == "ok" and j["agentes"] == IDS


def test_catalogo_de_agentes_publico():
    r = client.get("/api/agentes").json()
    assert [a["id"] for a in r] == IDS and all(a["card_url"].endswith("agent-card.json") for a in r)


def test_interfaz_web_y_swagger():
    assert "Red de agentes A2A" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/chat", "/api/consulta", "/api/consulta/stream", "/api/agentes", "/a2a", "/agents/agente_bryhan/a2a"):
        assert p in paths


# ── Agent Cards ──
@pytest.mark.parametrize("agente", IDS)
def test_agent_card_publica_y_declara_seguridad(agente):
    card = client.get(f"/agents/{agente}/.well-known/agent-card.json").json()
    assert card["name"] == agente and card["skills"] and "apiKey" in card["securitySchemes"]
    assert card["supportedInterfaces"][0]["url"].endswith(f"/agents/{agente}/a2a")


def test_card_original_de_alejandra_sigue_disponible():
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "agente_alejandra" and card["supportedInterfaces"][0]["url"].endswith("/a2a")


# ── acceso ──
def test_endpoints_protegidos_rechazan_sin_clave_o_con_clave_falsa():
    cuerpo = {"mensaje": "hola"}
    for ruta in ("/chat", "/api/consulta", "/api/consulta/stream"):
        assert client.post(ruta, json=cuerpo).status_code == 401, ruta
        assert client.post(ruta, json=cuerpo, headers={"X-API-Key": "mala"}).status_code == 401, ruta


@pytest.mark.parametrize("ruta", ["/a2a", "/agents/agente_alejandra/a2a", "/agents/agente_bryhan/a2a", "/agents/agente_carlos/a2a"])
def test_a2a_sin_clave_rechazado(ruta):
    assert client.post(ruta, json=jsonrpc("hola")).status_code == 401
    assert client.post(ruta, json=jsonrpc("hola"), headers={"X-API-Key": "mala"}).status_code == 401


def test_falla_cerrado_sin_claves_en_servidor(monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", [])
    assert client.post("/chat", json={"mensaje": "hola"}, headers=OK).status_code == 503
    assert client.post("/api/consulta", json={"mensaje": "hola"}, headers=OK).status_code == 503
    assert client.post("/a2a", json=jsonrpc("hola"), headers=OK).status_code == 503


def test_limite_de_consultas_por_minuto(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 2)
    security._ventanas.clear()
    codigos = [client.post("/chat", json={"mensaje": ""}, headers=OK).status_code for _ in range(3)]
    assert codigos == [422, 422, 429]  # las dos primeras pasan el acceso (fallan en validación); la tercera se limita
    security._ventanas.clear()


# ── validación ──
def test_entradas_invalidas():
    security._ventanas.clear()
    assert client.post("/chat", json={"mensaje": ""}, headers=OK).status_code == 422
    assert client.post("/chat", json={"mensaje": "x" * 5000}, headers=OK).status_code == 422
    assert client.post("/chat", json={"mensaje": "hola", "conversation_id": "a b/../"}, headers=OK).status_code == 422
    assert client.post("/chat", json={"mensaje": "hola", "agente": "no_existe"}, headers=OK).status_code == 422
    assert client.post("/api/consulta", json={"mensaje": "hola", "modo": "caos"}, headers=OK).status_code == 422
    r = client.post("/api/consulta", json={"mensaje": "hola", "agentes": ["fantasma"]}, headers=OK)
    assert r.status_code == 422 and "fantasma" in r.json()["detail"]


# ── fidelidad de cifras ──
def test_verificador_detecta_cifras_inventadas():
    aportes = ["La concentración es 35% y el límite 20% (NCG 365). RUT 77.987.654-3, multa 2025."]
    assert cifras_no_respaldadas("Concentración 35% vs límite 20% del RUT 77.987.654-3 en 2025.", aportes) == []
    assert cifras_no_respaldadas("La concentración sería 42% y vence 2031.", aportes) == ["2031", "42"]


def test_historial_protegido_y_404():
    assert client.get("/api/historial").status_code == 401
    assert client.get("/api/historial/algo").status_code == 401
    assert client.get("/api/historial", headers=OK).status_code == 200
    assert client.get("/api/historial/no-existe", headers=OK).status_code == 404
    assert client.get("/api/historial?limite=0", headers=OK).status_code == 422
    assert client.get("/health").json()["persistencia"] in ("memoria", "sqlite", "postgres")
