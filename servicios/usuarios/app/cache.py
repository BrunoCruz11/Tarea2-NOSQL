from redis.asyncio import Redis

from app import config

_redis: Redis | None = None


def conectar() -> None:
    global _redis
    _redis = Redis.from_url(config.REDIS_URL, decode_responses=True, socket_timeout=3)


async def desconectar() -> None:
    if _redis is not None:
        await _redis.aclose()


def obtener_redis() -> Redis:
    return _redis


async def estado() -> dict:
    try:
        await _redis.ping()
    except Exception as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True}
