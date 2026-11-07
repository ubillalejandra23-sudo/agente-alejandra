"""Control de acceso: clave validada en el servidor (X-API-Key o Authorization: Bearer) y límite de consultas."""
import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app import config

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clave de acceso configurada en API_KEYS")
bearer = HTTPBearer(auto_error=False, description="Alternativa: Authorization: Bearer <clave>")

_ventanas: dict[str, deque] = defaultdict(deque)


def clave_valida(candidata: str | None) -> bool:
    if not candidata:
        return False
    # Comparación en tiempo constante contra todas las claves (sin cortocircuito).
    resultado = False
    for k in config.API_KEYS:
        resultado |= hmac.compare_digest(candidata.encode(), k.encode())
    return resultado


def extraer_clave(request: Request) -> str | None:
    c = request.headers.get("x-api-key")
    if c:
        return c
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


def limitar(clave: str) -> None:
    """Ventana deslizante de 60 s por clave (se guarda un hash, no la clave). Lanza 429 al excederse."""
    if config.RATE_LIMIT_PER_MIN <= 0:
        return
    ident = hashlib.sha256(clave.encode()).hexdigest()[:16]
    ahora, v = time.monotonic(), _ventanas[ident]
    while v and ahora - v[0] > 60:
        v.popleft()
    if len(v) >= config.RATE_LIMIT_PER_MIN:
        raise HTTPException(429, f"Límite de {config.RATE_LIMIT_PER_MIN} consultas por minuto excedido. Espere unos segundos.",
                            headers={"Retry-After": "30"})
    v.append(ahora)


async def exigir_clave(
    request: Request,
    _k: str | None = Security(api_key_header),
    _b: HTTPAuthorizationCredentials | None = Security(bearer),
) -> None:
    """Dependencia FastAPI. Falla cerrado: si el servidor no tiene claves configuradas, responde 503."""
    if not config.API_KEYS:
        raise HTTPException(503, "El servidor no tiene API_KEYS configuradas; acceso deshabilitado.")
    clave = extraer_clave(request)
    if not clave_valida(clave):
        raise HTTPException(401, "Clave de acceso ausente o inválida.", headers={"WWW-Authenticate": "Bearer"})
    limitar(clave)
