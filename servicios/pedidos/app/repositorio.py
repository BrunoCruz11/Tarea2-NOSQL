"""Repositorio: la única capa que habla con MongoDB.

Colecciones:
  - pedidos: un documento por pedido (`_id` = pedido_id), con los items, el pago y
             el historial de la saga embebidos: siempre se leen y se escriben juntos.
  - eventos: un documento por cada paso o compensación de la saga, solo se agregan.
             Es el registro de eventos (Event Sourcing): con ellos se puede
             reconstruir cualquier pedido o auditar qué pasó y cuándo.
"""

from uuid import uuid4

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import db
from app.modelos import Evento, PasoHistorial, Pedido


class PedidoDuplicado(Exception):
    """Ya hay un pedido con esa Idempotency-Key."""


def _pedidos():
    return db.obtener_db()["pedidos"]


def _eventos():
    return db.obtener_db()["eventos"]


def _a_modelo(documento: dict) -> Pedido:
    documento = dict(documento)
    documento["pedido_id"] = documento.pop("_id")
    return Pedido.model_validate(documento)


def _a_evento(documento: dict) -> Evento:
    documento = dict(documento)
    documento["evento_id"] = documento.pop("_id")
    return Evento.model_validate(documento)


async def crear_indices() -> None:
    """Se llama al arrancar el servicio. create_index no hace nada si el índice ya existe."""
    # Único: dos eventos de un mismo pedido nunca pueden tener el mismo número.
    # También es el índice que usa listar_eventos para traerlos ordenados.
    await _eventos().create_index(
        [("pedido_id", ASCENDING), ("secuencia", ASCENDING)], unique=True, name="evento_por_pedido"
    )
    # Un pedido por Idempotency-Key. Parcial (solo donde la clave es un texto) para
    # que los pedidos guardados antes de este índice, que no la tienen, no choquen
    # entre sí como si todos tuvieran la clave null.
    await _pedidos().create_index(
        "idempotency_key",
        unique=True,
        name="idempotency_key_unica",
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )


async def crear(pedido: Pedido) -> None:
    documento = pedido.model_dump(mode="json", exclude={"pedido_id", "historial"})
    # Igual que con el email en usuarios: no se pregunta antes si la clave existe,
    # se inserta y el índice único rechaza el segundo de forma atómica.
    try:
        await _pedidos().insert_one({"_id": pedido.pedido_id, **documento, "historial": []})
    except DuplicateKeyError as error:
        raise PedidoDuplicado(pedido.idempotency_key) from error


async def obtener(pedido_id: str) -> Pedido | None:
    documento = await _pedidos().find_one({"_id": pedido_id})
    return _a_modelo(documento) if documento else None


async def obtener_por_clave(idempotency_key: str) -> Pedido | None:
    documento = await _pedidos().find_one({"idempotency_key": idempotency_key})
    return _a_modelo(documento) if documento else None


async def registrar_paso(pedido_id: str, entrada: PasoHistorial, cambios: dict) -> Pedido:
    """Agrega la entrada al historial, aplica los cambios del paso y guarda el evento.

    Son dos documentos (el pedido y el evento), así que van en una transacción:
    o quedan los dos o ninguno. Así el estado del pedido, su historial y los
    eventos nunca quedan desfasados.
    """
    actualizacion = {"$push": {"historial": entrada.model_dump()}}
    if cambios:
        actualizacion["$set"] = cambios

    async def escribir(sesion) -> dict:
        documento = await _pedidos().find_one_and_update(
            {"_id": pedido_id}, actualizacion, return_document=ReturnDocument.AFTER, session=sesion
        )
        await _eventos().insert_one({
            "_id": f"evt_{uuid4().hex[:12]}",
            "pedido_id": pedido_id,
            # El evento N es la entrada N del historial.
            "secuencia": len(documento["historial"]),
            **entrada.model_dump(),
            # Como lista y no como dict: los nombres con punto ("pago.estado_pago")
            # no se pueden usar cómodamente como claves de un documento de Mongo.
            "cambios": [{"campo": campo, "valor": valor} for campo, valor in cambios.items()],
        }, session=sesion)
        return documento

    async with await db.iniciar_sesion() as sesion:
        # with_transaction reintenta solo si la transacción choca con otra (error transitorio).
        documento = await sesion.with_transaction(escribir)
    return _a_modelo(documento)


async def listar_eventos(pedido_id: str) -> list[Evento]:
    cursor = _eventos().find({"pedido_id": pedido_id}).sort("secuencia", ASCENDING)
    return [_a_evento(documento) async for documento in cursor]
