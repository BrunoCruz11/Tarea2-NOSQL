"""Repositorio: la única capa que habla con MongoDB.

Un documento por pedido (`_id` = pedido_id), con los items, el pago y el historial
de la saga embebidos: siempre se leen y se escriben junto con el pedido.
"""

from pymongo import ReturnDocument

from app import db
from app.modelos import PasoHistorial, Pedido


def _coleccion():
    return db.obtener_db()["pedidos"]


def _a_modelo(documento: dict) -> Pedido:
    documento = dict(documento)
    documento["pedido_id"] = documento.pop("_id")
    return Pedido.model_validate(documento)


async def crear(pedido: Pedido) -> None:
    documento = pedido.model_dump(mode="json", exclude={"pedido_id", "historial"})
    await _coleccion().insert_one({"_id": pedido.pedido_id, **documento, "historial": []})


async def obtener(pedido_id: str) -> Pedido | None:
    documento = await _coleccion().find_one({"_id": pedido_id})
    return _a_modelo(documento) if documento else None


async def registrar_paso(pedido_id: str, entrada: PasoHistorial, cambios: dict) -> Pedido:
    """Agrega la entrada al historial y aplica los cambios del paso, en una sola escritura.

    Así el estado del pedido y su historial nunca quedan desfasados: si se ve el
    paso en el historial, su cambio (estado, datos del pago) también está guardado.
    """
    actualizacion = {"$push": {"historial": entrada.model_dump()}}
    if cambios:
        actualizacion["$set"] = cambios
    documento = await _coleccion().find_one_and_update(
        {"_id": pedido_id}, actualizacion, return_document=ReturnDocument.AFTER
    )
    return _a_modelo(documento)
