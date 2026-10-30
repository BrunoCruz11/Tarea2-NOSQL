"""Modelos (Pydantic): definen qué forma tienen los datos que entran y salen de la API."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductoCrear(BaseModel):
    """Body de POST /api/productos. El id lo pone el servicio."""

    model_config = ConfigDict(str_strip_whitespace=True)

    nombre: str = Field(min_length=1, examples=["Auriculares inalámbricos"])
    descripcion: str = Field(default="", examples=["Bluetooth 5.3, 30 horas de batería"])
    # Embebida como texto: se lee siempre junto con el producto y no tiene datos propios.
    categoria: str = Field(min_length=1, examples=["Electrónica"])
    precio: float = Field(gt=0, examples=[59.99])
    stock: int = Field(ge=0, examples=[10])


class Producto(ProductoCrear):
    """Producto completo, tal como lo devuelve la API (y como se guarda en la caché)."""

    id: str = Field(examples=["prod_3f9a1c2b7d4e"])
    # Al crear es obligatoria; el default es para los productos guardados antes de que existiera el campo.
    categoria: str = Field(default="Sin categoría", examples=["Electrónica"])


class ItemReserva(BaseModel):
    producto_id: str = Field(min_length=1, examples=["prod_3f9a1c2b7d4e"])
    cantidad: int = Field(gt=0, examples=[2])


class ReservaCrear(BaseModel):
    """Body del endpoint interno que usa pedidos para reservar el stock de un pedido."""

    pedido_id: str = Field(pattern=r"^ord_\w+$", examples=["ord_98765"])
    items: list[ItemReserva] = Field(min_length=1)

    # Si el mismo producto viene en dos renglones, los juntamos en uno: así cada
    # producto se descuenta (y se devuelve) una sola vez.
    @field_validator("items")
    @classmethod
    def juntar_productos_repetidos(cls, items: list[ItemReserva]) -> list[ItemReserva]:
        cantidades: dict[str, int] = {}
        for item in items:
            cantidades[item.producto_id] = cantidades.get(item.producto_id, 0) + item.cantidad
        return [ItemReserva(producto_id=p, cantidad=c) for p, c in cantidades.items()]


class EstadoReserva(StrEnum):
    PENDIENTE = "pendiente"  # descontando stock, todavía no terminó
    RESERVADA = "reservada"  # se descontó todo el pedido
    RECHAZADA = "rechazada"  # faltó stock de algún producto; lo descontado ya se devolvió
    LIBERADA = "liberada"  # pedidos compensó (canceló) la reserva; el stock ya se devolvió


class Reserva(BaseModel):
    pedido_id: str = Field(examples=["ord_98765"])
    estado: EstadoReserva
    items: list[ItemReserva]
    # Productos a los que ya se les descontó stock: es lo que hay que devolver al liberar.
    descontados: list[str] = []
    motivo: str | None = Field(default=None, examples=["Stock insuficiente de prod_3f9a1c2b7d4e"])
