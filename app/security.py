import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    return "if_" + secrets.token_urlsafe(32)


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def require_admin(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not x_admin_key or not _eq(x_admin_key, settings.admin_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé administrateur invalide ou absente (X-Admin-Key)")


def sensor_key_valid(point, provided: str | None, settings: Settings) -> bool:
    """Clé globale (SENSOR_API_KEY) ou clé propre au point."""
    if not provided:
        return False
    if settings.sensor_api_key and _eq(provided, settings.sensor_api_key):
        return True
    return bool(point is not None and point.api_key_hash and _eq(hash_key(provided), point.api_key_hash))


def global_sensor_key_valid(provided: str | None, settings: Settings) -> bool:
    return bool(provided and settings.sensor_api_key and _eq(provided, settings.sensor_api_key))
