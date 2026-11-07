"""Configuración leída del entorno (nunca valores fijos en el código)."""
import os

from dotenv import load_dotenv

load_dotenv()

AGENT_ID = "agente_alejandra"
MODELO = os.environ.get("MODELO", "claude-sonnet-5-5")
# Claves válidas separadas por coma (permite rotar sin cortar el servicio). Sin clave => el servidor se niega a atender.
API_KEYS = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]
# URL pública base del servicio (para la Agent Card). En Heroku: https://<app>.herokuapp.com
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
# Agente par (opcional) al que Alejandra consulta por A2A, p. ej. Benjamín.
# URL completa de la Agent Card del par (si está vacía, la colaboración A2A queda desactivada).
A2A_PEER_CARD_URL = os.environ.get("A2A_PEER_CARD_URL", "").strip()
A2A_PEER_NAME = os.environ.get("A2A_PEER_NAME", "agente_par")
A2A_PEER_API_KEY = os.environ.get("A2A_PEER_API_KEY", "")
A2A_PEER_TIMEOUT = float(os.environ.get("A2A_PEER_TIMEOUT", "60"))
