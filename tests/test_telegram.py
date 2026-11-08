"""Canal Telegram con una API de Telegram simulada (httpx.MockTransport): sin red, sin token real, sin modelo."""
import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, security
from app import canal_telegram as ct
from app.main import app
from app.orquestador import ErrorConsulta

TOKEN = "123456:TOKEN-FALSO-PARA-PRUEBAS"
PERSONA = 42


class FalsoTelegram:
    """Registra las llamadas a la API de Telegram y responde 'ok'."""

    def __init__(self, fallo_html=False):
        self.llamadas: list[tuple[str, dict]] = []
        self.fallo_html = fallo_html
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        metodo = request.url.path.rsplit("/", 1)[-1]
        cuerpo = json.loads(request.content or b"{}")
        self.llamadas.append((metodo, cuerpo))
        if self.fallo_html and metodo == "sendMessage" and cuerpo.get("parse_mode") == "HTML":
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
        self._n += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self._n, "username": "bot_de_prueba"}})

    def textos(self):
        return [c["text"] for m, c in self.llamadas if m == "sendMessage"]

    def metodos(self):
        return [m for m, _ in self.llamadas]


@pytest.fixture
def tg(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_IDS", {PERSONA})
    security._ventanas.clear()
    falso = FalsoTelegram()
    return ct.Telegram(transport=httpx.MockTransport(falso.handler)), falso


def update(texto, persona=PERSONA, chat_id=PERSONA, tipo="private", uid=1):
    return {"update_id": uid, "message": {"text": texto, "from": {"id": persona}, "chat": {"id": chat_id, "type": tipo}}}


def fake_consultar(registro, respuesta="## Conclusión\n**35%** vs límite de 20%"):
    async def _f(texto, agentes, modo, conv):
        registro.append((texto, agentes, conv))
        await asyncio.sleep(0.05)  # una consulta real tarda segundos: da tiempo al indicador "escribiendo…"
        return {"respuesta": respuesta, "solicitud_id": "abcdef1234567890", "ms_total": 12000}
    return _f


# ── formato ──
def test_a_html_convierte_y_escapa():
    h = ct.a_html("## Título\n- uno **negrita**\n> aviso\n| a | b |\n|---|---|\n| 1 | **2** |\n---\n<script>x</script> `cod`")
    assert "<b>Título</b>" in h and "• uno <b>negrita</b>" in h and "▎ aviso" in h
    assert "a · b" in h and "1 · <b>2</b>" in h and "|---" not in h
    assert "&lt;script&gt;" in h and "<script>" not in h and "<code>cod</code>" in h


def test_trocear_respeta_el_limite_y_no_pierde_texto():
    texto = "\n".join(f"línea {i} " + "x" * 120 for i in range(200)) + "\n" + "y" * 9000
    trozos = ct.trocear(texto, 3800)
    assert all(len(t) <= 3800 for t in trozos) and len(trozos) > 3
    assert "".join(trozos).replace("\n", "") == texto.replace("\n", "")


# ── acceso ──
def test_persona_no_autorizada_es_rechazada_sin_consultar(tg, monkeypatch):
    t, falso = tg
    llamadas = []
    monkeypatch.setattr(ct, "consultar", fake_consultar(llamadas))
    asyncio.run(t.procesar(update("¿Cumple la empresa X?", persona=99, chat_id=99)))
    assert llamadas == [] and "Acceso no autorizado" in falso.textos()[0] and "99" in falso.textos()[0]


def test_sin_ids_autorizados_rechaza_a_todos(tg, monkeypatch):
    t, falso = tg
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_IDS", set())
    llamadas = []
    monkeypatch.setattr(ct, "consultar", fake_consultar(llamadas))
    asyncio.run(t.procesar(update("hola")))
    assert llamadas == [] and "no autorizado" in falso.textos()[0].lower()


def test_ignora_chats_de_grupo(tg, monkeypatch):
    t, falso = tg
    monkeypatch.setattr(ct, "consultar", fake_consultar([]))
    asyncio.run(t.procesar(update("hola", tipo="group")))
    assert falso.llamadas == []


# ── comandos y consulta ──
def test_comandos(tg):
    t, falso = tg

    async def go():
        await t.procesar(update("/start", uid=1))
        await t.procesar(update("/agentes", uid=2))
        await t.procesar(update("/nueva", uid=3))
        await t.procesar(update("/inventado", uid=4))
    asyncio.run(go())
    s = falso.textos()
    assert "Red de agentes A2A" in s[0] and "agente_bryhan" in s[1] and "Conversación nueva" in s[2] and "no reconocido" in s[3]


def test_consulta_automatica_responde_y_limpia_el_progreso(tg, monkeypatch):
    t, falso = tg
    llamadas = []
    monkeypatch.setattr(ct, "consultar", fake_consultar(llamadas))
    asyncio.run(t.procesar(update("¿Cumple el fondo?")))
    assert llamadas == [("¿Cumple el fondo?", [], f"tg{PERSONA}")]          # enrutador automático, conversación por chat
    final = falso.textos()[-1]
    assert "<b>Conclusión</b>" in final and "<b>35%</b>" in final and "solicitud abcdef12" in final
    assert "deleteMessage" in falso.metodos() and "sendChatAction" in falso.metodos()


def test_comando_directo_a_un_agente_y_nueva_conversacion(tg, monkeypatch):
    t, _ = tg
    llamadas = []
    monkeypatch.setattr(ct, "consultar", fake_consultar(llamadas))

    async def go():
        await t.procesar(update("/bryhan ¿conviene automatizar?", uid=1))
        await t.procesar(update("/nueva", uid=2))
        await t.procesar(update("otra consulta", uid=3))
    asyncio.run(go())
    assert llamadas[0][1] == ["agente_bryhan"] and llamadas[0][2] == f"tg{PERSONA}"
    assert llamadas[1][1] == [] and llamadas[1][2].startswith(f"tg{PERSONA}-")        # /nueva cambia la conversación


def test_no_procesa_dos_veces_el_mismo_update(tg, monkeypatch):
    t, _ = tg
    llamadas = []
    monkeypatch.setattr(ct, "consultar", fake_consultar(llamadas))

    async def go():
        await t.procesar(update("hola", uid=7))
        await t.procesar(update("hola", uid=7))    # reintento de Telegram
    asyncio.run(go())
    assert len(llamadas) == 1


def test_falla_de_la_consulta_se_informa_como_falla(tg, monkeypatch):
    t, falso = tg

    async def falla(*a, **k):
        raise ErrorConsulta("Ningún agente pudo responder: agente_bryhan: timeout")
    monkeypatch.setattr(ct, "consultar", falla)
    asyncio.run(t.procesar(update("hola")))
    assert "No pude completar" in falso.textos()[-1] and "Ningún agente" in falso.textos()[-1]


def test_limite_de_consultas_por_persona(tg, monkeypatch):
    t, falso = tg
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 1)
    monkeypatch.setattr(ct, "consultar", fake_consultar([]))

    async def go():
        await t.procesar(update("uno", uid=1))
        await t.procesar(update("dos", uid=2))
    asyncio.run(go())
    assert "Demasiadas consultas" in falso.textos()[-1]


def test_si_telegram_rechaza_el_html_reintenta_en_texto_plano(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    falso = FalsoTelegram(fallo_html=True)
    t = ct.Telegram(transport=httpx.MockTransport(falso.handler))
    asyncio.run(t.enviar_html(1, "<b>hola</b> &amp; adiós"))
    enviados = [c for m, c in falso.llamadas if m == "sendMessage"]
    assert enviados[0].get("parse_mode") == "HTML" and "parse_mode" not in enviados[1] and enviados[1]["text"] == "hola & adiós"


# ── el token no se filtra ──
def test_el_token_no_aparece_en_errores_ni_logs(monkeypatch, caplog):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)

    def explota(request):
        raise httpx.ConnectError(f"fallo conectando a {request.url}")   # el mensaje incluiría la URL con el token
    t = ct.Telegram(transport=httpx.MockTransport(explota))
    with caplog.at_level(logging.DEBUG), pytest.raises(ct.ErrorTelegram) as e:
        asyncio.run(t.api("getMe"))
    assert "TOKEN-FALSO" not in str(e.value) and e.value.__cause__ is None and "TOKEN-FALSO" not in caplog.text


# ── webhook ──
client = TestClient(app)


def test_webhook_exige_canal_activo_y_secreto(monkeypatch):
    from app.canal_telegram import telegram
    recibidos = []
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "secreto-de-prueba")
    monkeypatch.setattr(telegram, "encolar", lambda u: recibidos.append(u))
    cuerpo = update("hola")
    monkeypatch.setattr(telegram, "modo", "off")
    assert client.post("/telegram/webhook", json=cuerpo).status_code == 404            # canal apagado
    monkeypatch.setattr(telegram, "modo", "webhook")
    assert client.post("/telegram/webhook", json=cuerpo).status_code == 401            # sin secreto
    assert client.post("/telegram/webhook", json=cuerpo, headers={"X-Telegram-Bot-Api-Secret-Token": "mal"}).status_code == 401
    assert recibidos == []
    ok = client.post("/telegram/webhook", json=cuerpo, headers={"X-Telegram-Bot-Api-Secret-Token": "secreto-de-prueba"})
    assert ok.status_code == 200 and recibidos == [cuerpo]


def test_iniciar_en_webhook_registra_la_url_con_el_secreto(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "secreto-123")
    monkeypatch.setattr(config, "TELEGRAM_MODO", "auto")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://mi-app.herokuapp.com")
    falso = FalsoTelegram()
    t = ct.Telegram(transport=httpx.MockTransport(falso.handler))
    asyncio.run(t.iniciar())
    sw = next(c for m, c in falso.llamadas if m == "setWebhook")
    assert t.modo == "webhook" and sw["url"] == "https://mi-app.herokuapp.com/telegram/webhook" and sw["secret_token"] == "secreto-123"


def test_en_local_http_el_modo_auto_no_toca_el_webhook(monkeypatch):
    """Evita que una prueba local desregistre el webhook de Heroku: sin https y sin pedir polling, no hace nada."""
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_MODO", "auto")
    monkeypatch.setattr(config, "PUBLIC_URL", "http://127.0.0.1:8000")
    falso = FalsoTelegram()
    t = ct.Telegram(transport=httpx.MockTransport(falso.handler))
    asyncio.run(t.iniciar())
    assert t.modo == "off" and falso.llamadas == []


def test_sin_secreto_el_webhook_queda_deshabilitado(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "")
    monkeypatch.setattr(config, "TELEGRAM_MODO", "webhook")
    falso = FalsoTelegram()
    t = ct.Telegram(transport=httpx.MockTransport(falso.handler))
    asyncio.run(t.iniciar())
    assert t.modo == "off" and "setWebhook" not in falso.metodos()


def test_reintenta_el_webhook_si_telegram_pide_esperar(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", "secreto-123")
    monkeypatch.setattr(config, "TELEGRAM_MODO", "webhook")
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))   # sin esperas reales en la prueba
    intentos = {"n": 0}

    def handler(request):
        metodo = request.url.path.rsplit("/", 1)[-1]
        if metodo == "setWebhook":
            intentos["n"] += 1
            if intentos["n"] == 1:
                return httpx.Response(429, json={"ok": False, "description": "Too Many Requests: retry after 1"})
        return httpx.Response(200, json={"ok": True, "result": {"username": "bot"}})
    t = ct.Telegram(transport=httpx.MockTransport(handler))
    asyncio.run(t.iniciar())
    assert t.modo == "webhook" and intentos["n"] == 2
