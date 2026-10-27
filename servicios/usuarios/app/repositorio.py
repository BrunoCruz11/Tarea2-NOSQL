"""Repositorio: la única capa que habla con MongoDB.

Recibe y devuelve modelos de Pydantic, así el resto del servicio no sabe nada
de `_id`, colecciones ni errores de pymongo.
"""

from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import db
from app.modelos import Usuario, UsuarioCrear


class UsuarioDuplicado(Exception):
    """Ya existe un usuario con el mismo email o el mismo documento."""

    def __init__(self, campo: str):
        self.campo = campo
        super().__init__(f"Ya existe un usuario con ese {campo}")


def _coleccion():
    return db.obtener_db()["usuarios"]


def _a_modelo(documento: dict) -> Usuario:
    # En Mongo el id se guarda en `_id`; en la API lo mostramos como `id`.
    documento = dict(documento)
    documento["id"] = documento.pop("_id")
    return Usuario.model_validate(documento)


async def crear_indices() -> None:
    """Se llama al arrancar el servicio. create_index no hace nada si el índice ya existe."""
    await _coleccion().create_index("email", unique=True, name="email_unico")
    await _coleccion().create_index(
        [("tipo_documento", 1), ("nro_documento", 1)], unique=True, name="documento_unico"
    )


async def crear(datos: UsuarioCrear) -> Usuario:
    documento = {
        "_id": f"usr_{uuid4().hex[:12]}",
        **datos.model_dump(),
        "historial_pedidos": [],
    }
    # No consultamos antes "¿existe ese email?": entre la consulta y el insert podría entrar
    # otro request igual. Insertamos directo y dejamos que el índice único lo rechace; eso
    # Mongo lo hace de forma atómica, así que nunca quedan dos usuarios con el mismo email.
    try:
        await _coleccion().insert_one(documento)
    except DuplicateKeyError as error:
        campos = (error.details or {}).get("keyPattern", {})
        raise UsuarioDuplicado("email" if "email" in campos else "documento") from error
    return _a_modelo(documento)


async def obtener(usuario_id: str) -> Usuario | None:
    documento = await _coleccion().find_one({"_id": usuario_id})
    return _a_modelo(documento) if documento else None


async def agregar_pedido(usuario_id: str, pedido_id: str) -> Usuario | None:
    """Agrega el pedido al historial y devuelve el usuario actualizado (None si no existe).

    Usamos $addToSet en vez de $push: si pedidos reintenta el mismo llamado,
    el id no se duplica (la operación es idempotente).
    """
    documento = await _coleccion().find_one_and_update(
        {"_id": usuario_id},
        {"$addToSet": {"historial_pedidos": pedido_id}},
        return_document=ReturnDocument.AFTER,
    )
    return _a_modelo(documento) if documento else None
