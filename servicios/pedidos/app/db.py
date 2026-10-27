from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app import config

_cliente: AsyncIOMotorClient | None = None


def conectar() -> None:
    global _cliente
    _cliente = AsyncIOMotorClient(config.MONGO_URL, serverSelectionTimeoutMS=3000, tz_aware=True)


def desconectar() -> None:
    if _cliente is not None:
        _cliente.close()


def obtener_db() -> AsyncIOMotorDatabase:
    return _cliente[config.MONGO_DB]


async def estado() -> dict:
    """Consulta el nodo de Mongo: si responde, a qué replica set pertenece y si es primario."""
    try:
        hello = await _cliente.admin.command("hello")
    except Exception as error:
        return {"ok": False, "error": str(error)}
    return {
        "ok": hello.get("isWritablePrimary", False),
        "replica_set": hello.get("setName"),
        "primario": hello.get("isWritablePrimary", False),
    }
