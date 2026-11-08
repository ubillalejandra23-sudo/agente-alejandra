"""Canal de atención por Telegram: la persona escribe al bot y recibe la respuesta de la red de agentes.

Flujo: mensaje de texto -> (autorización por ID de Telegram) -> orquestador A2A -> respuesta en el chat.
Modos: `webhook` (Heroku: Telegram llama a POST /telegram/webhook con un secreto) o `polling` (pruebas locales).

Seguridad:
  · Solo responde a los IDs de TELEGRAM_ALLOWED_IDS (vacío = rechaza a todos) y solo en chats privados.
  · El webhook exige la cabecera X-Telegram-Bot-Api-Secret-Token (TELEGRAM_WEBHOOK_SECRET).
  · El token va en la URL de la API de Telegram: NUNCA se registra (los errores se reducen a su tipo, sin URL).
  · Mismo límite de consultas por minuto que la API (por usuario de Telegram).
"""
import asyncio
import html
import logging
import re
import time
from collections import deque

import httpx
from fastapi import HTTPException

from app import config, security
from app.agentes.registro import AGENTES
from app.orquestador import ErrorConsulta, consultar, roster

log = logging.getLogger("telegram")
LIMITE = 3800  # margen bajo el máximo de 4096 caracteres de Telegram
DIRECTOS = {"/alejandra": "agente_alejandra", "/bryhan": "agente_bryhan", "/carlos": "agente_carlos"}

AYUDA = (
    "👋 <b>Red de agentes A2A</b>\n"
    "Escríbeme tu consulta y la reparto entre los agentes especialistas (se consultan por A2A) para darte una "
    "conclusión integrada con el aporte de cada uno.\n\n"
    "<b>Ejemplos</b>\n"
    "• ¿Cumple la empresa 77.987.654-3 con la normativa?\n"
    "• Revisa el fondo FM Inmobiliario y dime si conviene automatizar esa revisión con IA\n\n"
    "<b>Comandos</b>\n"
    "/agentes — quiénes son y qué hacen\n"
    "/alejandra, /bryhan, /carlos <i>pregunta</i> — consultar a uno solo\n"
    "/nueva — empezar una conversación nueva (olvidan lo anterior)\n"
    "/ayuda — este mensaje\n\n"
    "<i>Datos de SII/CMF/fondos simulados. Los agentes solo recomiendan; no ejecutan acciones.</i>")


class ErrorTelegram(RuntimeError):
    pass


# ── formato: Markdown de las respuestas -> HTML de Telegram ────────────────────────────────────────────────────────
def a_html(md: str) -> str:
    """Convierte el Markdown de las respuestas al HTML que admite Telegram. Cada línea queda autocontenida
    (etiquetas balanceadas por línea), así puede partirse en cualquier salto de línea."""
    salida = []
    for linea in md.replace("\r", "").split("\n"):
        l = html.escape(linea, quote=False)
        if re.match(r"^\s*\|[\s:|-]+\|\s*$", l):                    # separador de tabla: se omite
            continue
        if re.match(r"^\s*#{1,6}\s+", l):                            # títulos -> negrita
            l = "<b>" + re.sub(r"\*\*|`", "", re.sub(r"^\s*#{1,6}\s+", "", l)) + "</b>"
        elif re.match(r"^\s*---+\s*$", l):
            l = "──────────"
        elif re.match(r"^\s*\|.*\|\s*$", l):                         # fila de tabla -> celdas separadas por ·
            celdas = [c.strip() for c in l.strip().strip("|").split("|")]
            l = " · ".join(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", c) for c in celdas)
        else:
            l = re.sub(r"^(\s*)[-*•]\s+", r"\1• ", l)
            l = re.sub(r"^&gt;\s?", "▎ ", l)
            l = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", l)
            l = re.sub(r"`([^`]+)`", r"<code>\1</code>", l)
        salida.append(l)
    return "\n".join(salida)


def trocear(texto: str, limite: int = LIMITE) -> list[str]:
    """Parte el texto en mensajes de hasta `limite` caracteres, siempre en saltos de línea."""
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        while len(linea) > limite:                                   # línea gigante: corte duro
            if actual:
                trozos.append(actual)
                actual = ""
            trozos.append(linea[:limite])
            linea = linea[limite:]
        if actual and len(actual) + 1 + len(linea) > limite:
            trozos.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual.strip():
        trozos.append(actual)
    return trozos or [""]


def _plano(h: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", h))


class Telegram:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self.modo = "off"
        self.usuario_bot = ""
        self._vistos: deque = deque(maxlen=500)   # update_id ya procesados (Telegram reintenta si tarda)
        self._tareas: set[asyncio.Task] = set()
        self._gen: dict[int, int] = {}            # chat -> generación de conversación (/nueva)

    # ── API de Telegram (el token nunca sale en logs ni en errores) ──
    def _cliente(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30, transport=self._transport)
        return self._http

    async def api(self, metodo: str, timeout: float = 20, **params):
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{metodo}"
        try:
            r = await self._cliente().post(url, json=params, timeout=timeout)
        except Exception as e:
            raise ErrorTelegram(f"{metodo}: {type(e).__name__}") from None   # sin cadena: podría contener la URL
        try:
            datos = r.json()
        except ValueError:
            raise ErrorTelegram(f"{metodo}: respuesta no válida (HTTP {r.status_code})") from None
        if not datos.get("ok"):
            raise ErrorTelegram(f"{metodo}: {datos.get('description', 'error')}")
        return datos.get("result")

    async def enviar_html(self, chat_id: int, texto_html: str) -> int | None:
        """Envía (en varios mensajes si hace falta). Si Telegram rechaza el HTML, reintenta en texto plano."""
        ultimo = None
        for trozo in trocear(texto_html):
            try:
                ultimo = (await self.api("sendMessage", chat_id=chat_id, text=trozo, parse_mode="HTML",
                                         link_preview_options={"is_disabled": True}))["message_id"]
            except ErrorTelegram as e:
                if "parse entities" not in str(e):
                    raise
                ultimo = (await self.api("sendMessage", chat_id=chat_id, text=_plano(trozo)))["message_id"]
        return ultimo

    # ── ciclo de vida ──
    def activo(self) -> bool:
        return bool(config.TELEGRAM_BOT_TOKEN) and self.modo != "off"

    async def iniciar(self) -> None:
        if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_MODO == "off":
            return
        modo = config.TELEGRAM_MODO
        if modo == "auto":
            modo = "webhook" if config.PUBLIC_URL.startswith("https://") else "off"
        if modo not in ("webhook", "polling"):
            log.info("telegram: desactivado (modo=%s; PUBLIC_URL no es https y no se pidió polling)", modo)
            return
        if not config.TELEGRAM_ALLOWED_IDS:
            log.warning("telegram: TELEGRAM_ALLOWED_IDS vacío; el bot rechazará a todas las personas.")
        try:
            self.usuario_bot = (await self.api("getMe")).get("username", "")
            if modo == "webhook":
                if not config.TELEGRAM_WEBHOOK_SECRET:
                    log.error("telegram: falta TELEGRAM_WEBHOOK_SECRET; el webhook queda deshabilitado.")
                    return
                await self.api("setWebhook", url=f"{config.PUBLIC_URL}/telegram/webhook",
                               secret_token=config.TELEGRAM_WEBHOOK_SECRET, allowed_updates=["message"])
            else:
                await self.api("deleteWebhook")
                self._lanzar(self._sondear())
        except ErrorTelegram as e:
            log.error("telegram: no se pudo iniciar (%s)", e)
            return
        self.modo = modo
        log.info("telegram: @%s activo en modo %s (%d persona(s) autorizada(s))", self.usuario_bot, modo,
                 len(config.TELEGRAM_ALLOWED_IDS))

    async def detener(self) -> None:
        for t in list(self._tareas):
            t.cancel()
        if self._http:
            await self._http.aclose()
            self._http = None
        self.modo = "off"

    def _lanzar(self, corrutina) -> None:
        t = asyncio.create_task(corrutina)
        self._tareas.add(t)
        t.add_done_callback(self._tareas.discard)

    def encolar(self, update: dict) -> None:
        """Procesa el update en segundo plano: el webhook responde 200 de inmediato (Telegram reintenta si tarda)."""
        self._lanzar(self.procesar(update))

    async def _sondear(self) -> None:
        offset = None
        while True:
            try:
                ups = await self._getupdates(offset)
            except ErrorTelegram as e:
                log.warning("telegram polling: %s", e)
                await asyncio.sleep(5)
                continue
            for u in ups or []:
                offset = u["update_id"] + 1
                self.encolar(u)

    async def _getupdates(self, offset):
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
        try:
            r = await self._cliente().post(url, json={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
                                           timeout=40)
            datos = r.json()
        except Exception as e:
            raise ErrorTelegram(f"getUpdates: {type(e).__name__}") from None
        if not datos.get("ok"):
            raise ErrorTelegram(f"getUpdates: {datos.get('description', 'error')}")
        return datos.get("result")

    # ── atención de mensajes ──
    async def procesar(self, update: dict) -> None:
        try:
            uid = update.get("update_id")
            if uid in self._vistos:
                return
            self._vistos.append(uid)
            m = update.get("message")
            if not m or m.get("chat", {}).get("type") != "private":
                return
            chat_id, persona = m["chat"]["id"], (m.get("from") or {}).get("id")
            if persona not in config.TELEGRAM_ALLOWED_IDS:
                log.warning("telegram: persona no autorizada id=%s", persona)
                await self.enviar_html(chat_id, "⛔ <b>Acceso no autorizado.</b>\nTu ID de Telegram es "
                                                f"<code>{persona}</code>. Pide a quien administra este bot que lo agregue.")
                return
            texto = (m.get("text") or "").strip()
            if not texto:
                await self.enviar_html(chat_id, "Por ahora solo entiendo mensajes de texto. Escribe tu consulta.")
                return
            await self._atender(chat_id, persona, texto)
        except ErrorTelegram as e:
            log.warning("telegram: %s", e)
        except Exception as e:
            log.error("telegram: fallo inesperado (%s)", type(e).__name__)

    async def _atender(self, chat_id: int, persona: int, texto: str) -> None:
        if texto.startswith("/"):
            cmd, _, resto = texto.partition(" ")
            cmd = cmd.split("@")[0].lower()
            resto = resto.strip()
            if cmd in ("/start", "/ayuda", "/help"):
                return await self._resp(chat_id, AYUDA)
            if cmd == "/agentes":
                filas = "\n".join(f"• <b>{html.escape(m.nombre)}</b> (<code>{m.id}</code>)\n   {html.escape(m.especialidad)}"
                                  for m in roster().values())
                return await self._resp(chat_id, "<b>Agentes disponibles</b>\n" + filas)
            if cmd == "/nueva":
                self._gen[chat_id] = int(time.time())
                return await self._resp(chat_id, "🆕 Conversación nueva. Los agentes no recordarán lo anterior.")
            if cmd in DIRECTOS:
                if not resto:
                    return await self._resp(chat_id, f"Uso: <code>{cmd} tu pregunta</code>")
                return await self._consultar(chat_id, persona, resto, [DIRECTOS[cmd]])
            return await self._resp(chat_id, "Comando no reconocido. Escribe /ayuda.")
        await self._consultar(chat_id, persona, texto, [])

    async def _resp(self, chat_id: int, texto_html: str) -> None:
        await self.enviar_html(chat_id, texto_html)

    async def _consultar(self, chat_id: int, persona: int, texto: str, agentes: list[str]) -> None:
        if len(texto) > 4000:
            return await self._resp(chat_id, "Tu mensaje supera los 4000 caracteres. Resúmelo, por favor.")
        try:
            security.limitar(f"tg:{persona}")
        except HTTPException:
            return await self._resp(chat_id, "⏱ Demasiadas consultas seguidas. Espera un momento e intenta de nuevo.")
        gen = self._gen.get(chat_id)
        conv = f"tg{chat_id}" + (f"-{gen}" if gen else "")
        log.info("telegram consulta chat=%s conv=%s agentes=%s chars=%d", chat_id, conv, agentes or "auto", len(texto))

        progreso = await self.enviar_html(chat_id, "⏳ <i>Consultando a la red de agentes…</i>")
        escribiendo = asyncio.create_task(self._escribiendo(chat_id))
        try:
            res = await consultar(texto, agentes, "paralelo", conv)
            cuerpo = a_html(res["respuesta"]) + f"\n\n<i>solicitud {res['solicitud_id'][:8]} · {res['ms_total'] / 1000:.0f} s</i>"
        except ErrorConsulta as e:
            cuerpo = f"⚠ <b>No pude completar la consulta.</b>\n{html.escape(e.detalle)}"
        except Exception as e:
            log.error("telegram: fallo de la consulta (%s)", type(e).__name__)
            cuerpo = "⚠ <b>Ocurrió un error al procesar la consulta.</b> Intenta de nuevo en un momento."
        finally:
            escribiendo.cancel()
        if progreso:
            try:
                await self.api("deleteMessage", chat_id=chat_id, message_id=progreso)
            except ErrorTelegram:
                pass
        await self.enviar_html(chat_id, cuerpo)

    async def _escribiendo(self, chat_id: int) -> None:
        while True:
            try:
                await self.api("sendChatAction", chat_id=chat_id, action="typing")
            except ErrorTelegram:
                pass
            await asyncio.sleep(4)


telegram = Telegram()
