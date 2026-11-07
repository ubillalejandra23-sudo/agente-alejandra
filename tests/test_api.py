"""Pruebas de seguridad y validación (no llaman al modelo). Las pruebas con modelo real se omiten sin ANTHROPIC_API_KEY."""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)
OK = {"X-API-Key": "clave-de-prueba"}


def jsonrpc(texto, ctx=None):
    return {"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {"message": {
        "role": "user", "messageId": uuid.uuid4().hex, "contextId": ctx or str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": texto}]}}}


def test_health_publico():
    assert client.get("/health").json()["status"] == "ok"


def test_chat_sin_clave_rechazado():
    assert client.post("/chat", json={"mensaje": "hola"}).status_code == 401


def test_chat_clave_invalida_rechazado():
    assert client.post("/chat", json={"mensaje": "hola"}, headers={"X-API-Key": "mala"}).status_code == 401


def test_chat_falla_cerrado_sin_claves_en_servidor(monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", [])
    assert client.post("/chat", json={"mensaje": "hola"}, headers=OK).status_code == 503


def test_chat_entrada_invalida():
    assert client.post("/chat", json={"mensaje": ""}, headers=OK).status_code == 422
    assert client.post("/chat", json={"mensaje": "x" * 5000}, headers=OK).status_code == 422
    assert client.post("/chat", json={"mensaje": "hola", "conversation_id": "a b/../"}, headers=OK).status_code == 422


def test_agent_card_publica_y_declara_seguridad():
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "agente_alejandra" and card["skills"]
    assert "apiKey" in card["securitySchemes"]


def test_a2a_sin_clave_rechazado():
    assert client.post("/a2a", json=jsonrpc("hola")).status_code == 401
    assert client.post("/a2a", json=jsonrpc("hola"), headers={"X-API-Key": "mala"}).status_code == 401


def test_swagger_disponible():
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/chat" in paths and "/a2a" in paths


needs_llm = pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requiere ANTHROPIC_API_KEY")


@needs_llm
def test_chat_real_usa_herramientas_y_conserva_datos():
    r = client.post("/chat", headers=OK, json={"mensaje": "Revisa el cumplimiento normativo del fondo FM Inmobiliario"})
    assert r.status_code == 200
    t = r.json()["respuesta"]
    assert "35" in t and "20" in t  # concentración 35% vs límite 20% (cifras de la herramienta)


@needs_llm
def test_a2a_real_message_send():
    r = client.post("/a2a", headers=OK, json=jsonrpc("¿Qué observaciones tiene en el SII el RUT 77.987.654-3?"))
    assert r.status_code == 200 and "result" in r.json(), r.text
    assert "IVA" in str(r.json()["result"])
