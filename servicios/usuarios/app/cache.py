"""Acceso a Redis, usado como caché de lectura.

Si Redis falla, las funciones no levantan error: leer() devuelve None (como si no
estuviera en caché) y el servicio sigue respondiendo desde Mongo. La caché acelera,
pero no es imprescindible: priorizamos disponibilidad.
"""

import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app import config

log = logging.getLogger(__name__)

_redis: Redis | None = None


def conectar() -> None:
    global _redis
    # Timeouts cortos: si Redis no responde rápido, conviene ir directo a Mongo
    # en vez de hacer esperar al cliente.
    _redis = Redis.from_url(
        config.REDIS_URL, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5
    )


async def desconectar() -> None:
    if _redis is not None:
        await _redis.aclose()


async def estado() -> dict:
    try:
        await _redis.ping()
    except Exception as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True}


async def leer(clave: str) -> dict | None:
    try:
        valor = await _redis.get(clave)
    except RedisError as error:
        log.warning("No se pudo leer %s de Redis: %s", clave, error)
        return None
    return json.loads(valor) if valor is not None else None


async def guardar(clave: str, valor: dict) -> None:
    """Guarda con TTL: pasado ese tiempo Redis borra la clave y la próxima lectura va a Mongo."""
    try:
        await _redis.set(clave, json.dumps(valor), ex=config.CACHE_TTL_SEGUNDOS)
    except RedisError as error:
        log.warning("No se pudo guardar %s en Redis: %s", clave, error)


async def borrar(clave: str) -> None:
    try:
        await _redis.delete(clave)
    except RedisError as error:
        # Si no se pudo borrar, el dato viejo vive como mucho hasta que venza el TTL.
        log.warning("No se pudo borrar %s de Redis: %s", clave, error)
