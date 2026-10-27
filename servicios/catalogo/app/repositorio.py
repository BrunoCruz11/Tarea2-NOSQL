"""Repositorio: la única capa que habla con MongoDB.

Colecciones:
  - productos: un documento por producto, con su stock.
  - reservas:  un documento por pedido (`_id` = pedido_id). Sirve de clave de
               idempotencia y de registro de qué se descontó, para poder devolverlo.

Cada función hace UNA operación de Mongo, y cada una es atómica sobre su documento.
La lógica que las combina (la reserva y su compensación) está en servicio.py.
"""

from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import db
from app.modelos import EstadoReserva, ItemReserva, Producto, ProductoCrear, Reserva


class ReservaExistente(Exception):
    """Ya hay una reserva para ese pedido_id (es un reintento)."""


def _productos():
    return db.obtener_db()["productos"]


def _reservas():
    return db.obtener_db()["reservas"]


def _a_producto(documento: dict) -> Producto:
    documento = dict(documento)
    documento["id"] = documento.pop("_id")
    return Producto.model_validate(documento)


def _a_reserva(documento: dict) -> Reserva:
    documento = dict(documento)
    documento["pedido_id"] = documento.pop("_id")
    return Reserva.model_validate(documento)


# ---------- Productos ----------

async def crear_producto(datos: ProductoCrear) -> Producto:
    documento = {"_id": f"prod_{uuid4().hex[:12]}", **datos.model_dump()}
    await _productos().insert_one(documento)
    return _a_producto(documento)


async def obtener_producto(producto_id: str) -> Producto | None:
    documento = await _productos().find_one({"_id": producto_id})
    return _a_producto(documento) if documento else None


async def descontar_stock(producto_id: str, cantidad: int) -> bool:
    """Descuenta `cantidad` solo si alcanza el stock. Devuelve si pudo.

    Esto es lo que evita la sobreventa: la condición (stock >= cantidad) y el
    descuento van en la MISMA operación, y Mongo la aplica de forma atómica sobre
    el documento. Si llegan 100 compras a la vez por la última unidad, solo una
    encuentra stock >= 1; para las demás el filtro ya no matchea y no se modifica nada.

    Lo que NO hay que hacer es leer el stock, comparar en Python y después escribir:
    entre la lectura y la escritura otro request puede llevarse la misma unidad.
    """
    resultado = await _productos().update_one(
        {"_id": producto_id, "stock": {"$gte": cantidad}},
        {"$inc": {"stock": -cantidad}},
    )
    return resultado.modified_count == 1


async def devolver_stock(producto_id: str, cantidad: int) -> None:
    await _productos().update_one({"_id": producto_id}, {"$inc": {"stock": cantidad}})


# ---------- Reservas ----------

async def crear_reserva(pedido_id: str, items: list[ItemReserva]) -> None:
    """Registra la reserva en estado pendiente. Si ya existe levanta ReservaExistente.

    Igual que con el email en usuarios: no preguntamos antes si existe. El `_id`
    es único, así que si llegan dos requests con el mismo pedido_id a la vez,
    solo uno logra insertar; el otro sabe que es un reintento.
    """
    try:
        await _reservas().insert_one({
            "_id": pedido_id,
            "estado": EstadoReserva.PENDIENTE,
            "items": [item.model_dump() for item in items],
            "descontados": [],
        })
    except DuplicateKeyError as error:
        raise ReservaExistente(pedido_id) from error


async def crear_reserva_liberada(pedido_id: str) -> bool:
    """Deja asentado que el pedido se liberó sin haber reservado nada.

    Cubre el caso en que el "liberar" llega antes que el "reservar" (por ejemplo,
    pedidos tuvo un timeout y compensó). Si después llega el reservar, choca con
    este documento y no descuenta. Devuelve False si ya había una reserva.
    """
    try:
        await _reservas().insert_one(
            {"_id": pedido_id, "estado": EstadoReserva.LIBERADA, "items": [], "descontados": []}
        )
    except DuplicateKeyError:
        return False
    return True


async def obtener_reserva(pedido_id: str) -> Reserva | None:
    documento = await _reservas().find_one({"_id": pedido_id})
    return _a_reserva(documento) if documento else None


async def registrar_descuento(pedido_id: str, producto_id: str) -> bool:
    """Anota que a ese producto ya se le descontó stock. Solo si la reserva sigue pendiente.

    Devuelve False si la reserva ya no está pendiente (la liberaron mientras tanto):
    en ese caso quien llamó tiene que devolver ese stock él mismo.
    """
    resultado = await _reservas().update_one(
        {"_id": pedido_id, "estado": EstadoReserva.PENDIENTE},
        {"$push": {"descontados": producto_id}},
    )
    return resultado.modified_count == 1


async def cambiar_estado(
    pedido_id: str, desde: list[EstadoReserva], hacia: EstadoReserva, motivo: str | None = None
) -> Reserva | None:
    """Pasa la reserva a `hacia` solo si está en alguno de los estados `desde`.

    Devuelve la reserva actualizada, o None si estaba en otro estado. Como es atómico,
    si dos requests intentan cerrar la misma reserva solo uno lo logra, y ese es el
    único que devuelve el stock: así nunca se devuelve dos veces.
    """
    documento = await _reservas().find_one_and_update(
        {"_id": pedido_id, "estado": {"$in": desde}},
        {"$set": {"estado": hacia, "motivo": motivo}},
        return_document=ReturnDocument.AFTER,
    )
    return _a_reserva(documento) if documento else None
