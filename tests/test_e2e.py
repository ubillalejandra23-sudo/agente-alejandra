"""Pruebas de extremo a extremo con el modelo real y un servidor uvicorn de verdad (el orquestador se llama a sí
mismo por A2A, así que TestClient no alcanza). Se omiten sin ANTHROPIC_API_KEY. Consumen tokens del modelo."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from app import config  # noqa: F401  (carga .env)

pytestmark = pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="requiere ANTHROPIC_API_KEY")
CLAVE = "clave-e2e"


@pytest.fixture(scope="module")
def servidor():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    env = dict(os.environ, API_KEYS=CLAVE, PORT=str(puerto), PUBLIC_URL=f"http://127.0.0.1:{puerto}",
               INTERNAL_URL=f"http://127.0.0.1:{puerto}")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(puerto)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(60):
        try:
            if httpx.get(base + "/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        p.kill()
        pytest.fail("el servidor no arrancó")
    yield base
    p.terminate()


H = {"X-API-Key": CLAVE}


def test_chat_directo_con_datos_exactos(servidor):
    r = httpx.post(servidor + "/chat", headers=H, timeout=120,
                   json={"mensaje": "Revisa el cumplimiento normativo del fondo FM Inmobiliario"})
    assert r.status_code == 200
    t = r.json()["respuesta"]
    assert "35" in t and "20" in t  # concentración 35% vs límite 20% (cifras de la herramienta)


def test_a2a_directo_a_un_agente(servidor):
    r = httpx.post(servidor + "/a2a", headers=H, timeout=120,
                   json={"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {"message": {
                       "role": "user", "messageId": uuid.uuid4().hex, "contextId": str(uuid.uuid4()),
                       "parts": [{"kind": "text", "text": "¿Qué observaciones tiene en el SII el RUT 77.987.654-3?"}]}}})
    assert r.status_code == 200 and "IVA" in r.text


def test_consulta_colaborativa_por_a2a_conserva_cifras(servidor):
    r = httpx.post(servidor + "/api/consulta", headers=H, timeout=180, json={
        "mensaje": "Revisa el fondo FM Inmobiliario (concentración) y dime si conviene automatizar esa revisión con IA.",
        "agentes": ["agente_alejandra", "agente_bryhan"], "modo": "paralelo"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert {a["id"] for a in j["agentes"]} == {"agente_alejandra", "agente_bryhan"}
    assert all(a["estado"] == "ok" and a["context_id"] and a["ms"] > 0 for a in j["agentes"])
    assert "Conclusión integrada" in j["respuesta"]
    ale = next(a for a in j["agentes"] if a["id"] == "agente_alejandra")["respuesta"]
    assert ale in j["respuesta"]  # el aporte de cada agente se entrega SIN modificar
    assert "35" in ale and j["verificacion"]["sintesis_generada"]


def test_consulta_automatica_y_streaming(servidor):
    eventos = []
    with httpx.stream("POST", servidor + "/api/consulta/stream", headers=H, timeout=180,
                      json={"mensaje": "¿Cumple la empresa 77.987.654-3 con la normativa del SII y la CMF?"}) as r:
        assert r.status_code == 200
        for linea in r.iter_lines():
            if linea.strip():
                eventos.append(json.loads(linea))
    tipos = [e["evento"] for e in eventos]
    assert tipos[0] == "inicio" and "enrutado" in tipos and tipos[-1] == "final"
    final = eventos[-1]
    assert final["automatico"] is True and "agente_alejandra" in [a["id"] for a in final["agentes"]]


def test_agente_externo_caido_se_informa_como_error(servidor):
    """Un agente que falla no se presenta como éxito: con un único agente caído la consulta responde 502."""
    p_env = dict(os.environ, API_KEYS=CLAVE, A2A_EXTERNOS=json.dumps(
        [{"id": "agente_fantasma", "nombre": "Fantasma", "card_url": "http://127.0.0.1:9/.well-known/agent-card.json"}]),
        PORT="0")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    p_env.update(PORT=str(puerto), PUBLIC_URL=f"http://127.0.0.1:{puerto}", INTERNAL_URL=f"http://127.0.0.1:{puerto}")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(puerto)], env=p_env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{puerto}"
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        r = httpx.post(base + "/api/consulta", headers=H, timeout=60,
                       json={"mensaje": "hola", "agentes": ["agente_fantasma"]})
        assert r.status_code == 502 and "agente_fantasma" in r.json()["detail"]
    finally:
        p.terminate()
