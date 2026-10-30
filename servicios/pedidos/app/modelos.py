"""Modelos (Pydantic): definen qué forma tienen los datos que entran y salen de la API.

El Pedido sigue la estructura del anexo de la letra, más el historial de la saga.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MetodoPago(StrEnum):
    TARJETA_CREDITO = "TARJETA_CREDITO"
    TARJETA_DEBITO = "TARJETA_DEBITO"
    TRANSFERENCIA = "TRANSFERENCIA"
    # Para la demo: la pasarela simulada siempre rechaza este método.
    TARJETA_RECHAZADA = "TARJETA_RECHAZADA"


class ItemPedido(BaseModel):
    # Con el prefijo obligatorio el id no puede traer "/" ni "..": se usa para armar
    # la URL del catálogo y así no se puede apuntar a otra ruta.
    producto_id: str = Field(pattern=r"^prod_\w+$", examples=["prod_3f9a1c2b7d4e"])
    cantidad: int = Field(gt=0, examples=[2])


class PedidoCrear(BaseModel):
    """Body de POST /api/pedidos.

    Solo se toma usuario_id, metodo_pago y, de cada item, producto_id y cantidad.
    Si el cliente manda precios o un total, Pydantic los descarta: el total se
    calcula con los precios del catálogo.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    usuario_id: str = Field(pattern=r"^usr_\w+$", examples=["usr_3f9a1c2b7d4e"])
    items: list[ItemPedido] = Field(min_length=1)
    metodo_pago: MetodoPago = Field(examples=[MetodoPago.TARJETA_CREDITO])

    # Si el mismo producto viene en dos renglones, los juntamos en uno (igual que catálogo).
    @field_validator("items")
    @classmethod
    def juntar_productos_repetidos(cls, items: list[ItemPedido]) -> list[ItemPedido]:
        cantidades: dict[str, int] = {}
        for item in items:
            cantidades[item.producto_id] = cantidades.get(item.producto_id, 0) + item.cantidad
        return [ItemPedido(producto_id=p, cantidad=c) for p, c in cantidades.items()]


class LineaPedido(BaseModel):
    producto_id: str = Field(examples=["prod_001"])
    cantidad: int = Field(examples=[2])
    precio_unitario: float = Field(examples=[25.50])


class EstadoPedido(StrEnum):
    PENDIENTE = "PENDIENTE"  # recién creado, todavía no se reservó stock
    PROCESANDO = "PROCESANDO"  # stock reservado, procesando el pago
    CONFIRMADO = "CONFIRMADO"  # pagado y en el historial del usuario
    CANCELADO = "CANCELADO"  # algún paso falló y se compensaron los anteriores


class EstadoPago(StrEnum):
    PENDIENTE = "PENDIENTE"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"
    REEMBOLSADO = "REEMBOLSADO"


class Pago(BaseModel):
    metodo: MetodoPago
    estado_pago: EstadoPago
    transaccion_id: str | None = Field(default=None, examples=["tx_456789"])


class ResultadoPaso(StrEnum):
    OK = "OK"
    FALLO = "FALLO"


class PasoHistorial(BaseModel):
    """Una entrada del historial: qué paso (o compensación) de la saga se ejecutó y cómo salió."""

    paso: str = Field(examples=["RESERVAR_STOCK"])
    resultado: ResultadoPaso
    detalle: str = Field(examples=["Stock reservado en catálogo"])
    fecha: datetime


class Pedido(BaseModel):
    pedido_id: str = Field(examples=["ord_98765"])
    usuario_id: str = Field(examples=["usr_12345"])
    estado: EstadoPedido
    items: list[LineaPedido]
    total: float = Field(examples=[51.00])
    pago: Pago
    # La manda el cliente en el header Idempotency-Key; None en pedidos anteriores a ese header.
    idempotency_key: str | None = Field(default=None, examples=["compra-7f3a9c"])
    historial: list[PasoHistorial] = []


class CambioEvento(BaseModel):
    campo: str = Field(examples=["pago.estado_pago"])
    valor: Any = Field(examples=["APROBADO"])


class Evento(BaseModel):
    """Un hecho de la saga, guardado aparte en la colección `eventos`.

    Los eventos solo se agregan, nunca se modifican ni se borran. Aplicando los
    `cambios` de todos los eventos de un pedido, en orden de `secuencia`, se llega
    al mismo estado que tiene el documento del pedido.
    """

    evento_id: str = Field(examples=["evt_3f9a1c2b7d4e"])
    pedido_id: str = Field(examples=["ord_98765"])
    secuencia: int = Field(examples=[1])  # 1, 2, 3...: el orden dentro del pedido
    paso: str = Field(examples=["RESERVAR_STOCK"])
    resultado: ResultadoPaso
    detalle: str = Field(examples=["Stock reservado en catálogo"])
    cambios: list[CambioEvento] = []
    fecha: datetime
