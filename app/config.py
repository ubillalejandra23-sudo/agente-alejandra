"""Configuración leída del entorno (nunca valores fijos en el código)."""
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("config")

def _si(nombre: str) -> bool:
    return (os.environ.get(nombre) or "").strip().lower() in ("1", "true", "si", "sí", "yes", "on")


# Modo económico: especialistas con Haiku y topes más bajos (útil para desarrollar y probar sin gastar de más).
ECONOMICO = _si("ECONOMICO")
MODELO_BARATO = "claude-haiku-5-5"
# Especialistas (los que usan herramientas y entregan cifras): Sonnet por defecto; Haiku en modo económico.
MODELO = MODELO_BARATO if ECONOMICO else (os.environ.get("MODELO") or "claude-sonnet-5-5")
# Enrutador, Coordinador y conclusión integrada: tareas livianas -> Haiku por defecto.
MODELO_ROUTER = os.environ.get("MODELO_ROUTER") or MODELO_BARATO
# Topes de longitud de respuesta (una respuesta cortada se avisa) y ventana de historial enviada al modelo.
MAX_TOKENS = int(os.environ.get("MAX_TOKENS") or (1500 if ECONOMICO else 2500))
MAX_TOKENS_AUX = int(os.environ.get("MAX_TOKENS_AUX") or 1500)
HISTORIAL_MAX_MENSAJES = int(os.environ.get("HISTORIAL_MAX_MENSAJES") or (12 if ECONOMICO else 24))
PROMPT_CACHE = (os.environ.get("PROMPT_CACHE") or "1").strip().lower() not in ("0", "false", "no", "off")

# Claves válidas separadas por coma (permite rotar sin cortar el servicio). Sin clave => el servidor se niega a atender.
# La clave de ejemplo de .env.example es pública (está en GitHub): si alguien la deja sin cambiar, NO se acepta como válida.
CLAVE_DE_EJEMPLO = "clave-ficticia-cambiar-por-una-larga-y-aleatoria"
_claves = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]
if CLAVE_DE_EJEMPLO in _claves:
    log.error("API_KEYS contiene la clave de ejemplo de .env.example: se ignora. Genere una propia (ver .env.example).")
API_KEYS = [k for k in _claves if k != CLAVE_DE_EJEMPLO]
# URL pública base del servicio (aparece en las Agent Cards). En Heroku: https://<app>.herokuapp.com
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or "http://127.0.0.1:8000").rstrip("/")
# URL con la que el propio servicio se llama a sí mismo por A2A (Heroku entrega el puerto en $PORT).
INTERNAL_URL = (os.environ.get("INTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT') or '8000'}").rstrip("/")

# Control de costos: consultas por minuto y por clave en /chat y /api/consulta (0 = sin límite).
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN") or "20")
A2A_TIMEOUT = float(os.environ.get("A2A_TIMEOUT") or "90")  # segundos por agente


def agentes_externos() -> list[dict]:
    """Agentes A2A de otras personas/servicios. JSON: [{"id","nombre","especialidad","card_url","api_key"}]."""
    crudo = os.environ.get("A2A_EXTERNOS", "").strip()
    if not crudo:
        return []
    try:
        datos = json.loads(crudo)
        return [d for d in datos if d.get("id") and d.get("card_url")]
    except (ValueError, AttributeError):
        log.error("A2A_EXTERNOS no es un JSON válido; se ignora.")
        return []


# ── Canal Telegram (opcional: sin TELEGRAM_BOT_TOKEN queda desactivado) ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
# IDs numéricos de Telegram autorizados, separados por coma. Vacío = el bot rechaza a todos (falla cerrado).
TELEGRAM_ALLOWED_IDS = {int(x) for x in (os.environ.get("TELEGRAM_ALLOWED_IDS") or "").replace(" ", "").split(",")
                        if x.lstrip("-").isdigit()}
# Secreto que Telegram envía en cada llamada al webhook (A-Z a-z 0-9 _ -).
TELEGRAM_WEBHOOK_SECRET = (os.environ.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()
# auto: webhook si PUBLIC_URL es https, apagado si no | webhook | polling (pruebas locales) | off
TELEGRAM_MODO = (os.environ.get("TELEGRAM_MODO") or "auto").strip().lower()
