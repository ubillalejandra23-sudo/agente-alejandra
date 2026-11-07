"""Control de acceso: clave validada en el servidor (cabecera X-API-Key o Authorization: Bearer)."""
import hmac

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app import config

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Clave de acceso configurada en API_KEYS")
bearer = HTTPBearer(auto_error=False, description="Alternativa: Authorization: Bearer <clave>")


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


async def exigir_clave(
    request: Request,
    _k: str | None = Security(api_key_header),
    _b: HTTPAuthorizationCredentials | None = Security(bearer),
) -> None:
    """Dependencia FastAPI. Falla cerrado: si el servidor no tiene claves configuradas, responde 503."""
    if not config.API_KEYS:
        raise HTTPException(503, "El servidor no tiene API_KEYS configuradas; acceso deshabilitado.")
    if not clave_valida(extraer_clave(request)):
        raise HTTPException(401, "Clave de acceso ausente o inválida.", headers={"WWW-Authenticate": "Bearer"})
