"""Configuración leída del entorno (nunca valores fijos en el código)."""
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("config")

MODELO = os.environ.get("MODELO") or "claude-sonnet-5-5"
MODELO_ROUTER = os.environ.get("MODELO_ROUTER") or MODELO  # enrutador y síntesis; puede ser un modelo más barato

# Claves válidas separadas por coma (permite rotar sin cortar el servicio). Sin clave => el servidor se niega a atender.
API_KEYS = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]
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
