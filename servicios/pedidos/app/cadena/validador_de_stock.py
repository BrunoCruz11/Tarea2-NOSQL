import asyncio

from fastapi import status

from app import clientes
from app.cadena.base import ContextoPedido, ErrorDePedido, Manejador


class ValidadorDeStock(Manejador):
    """Le pregunta al catálogo por cada producto: que exista y que tenga stock suficiente.

    Es un control previo para rechazar rápido lo que seguro no se puede vender. NO
    garantiza que no haya sobreventa: entre esta consulta y la reserva otro pedido
    puede llevarse el stock (y el catálogo puede responder desde su caché). La garantía
    la da la reserva atómica del catálogo, que se hace en la saga.
    """

    async def procesar(self, contexto: ContextoPedido) -> None:
        items = contexto.datos.items
        try:
            # Consultamos todos los productos a la vez en vez de uno por uno.
            productos = await asyncio.gather(
                *(clientes.obtener_producto(item.producto_id) for item in items)
            )
        except clientes.ServicioNoDisponible as error:
            raise ErrorDePedido(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

        for item, producto in zip(items, productos):
            if producto is None:
                raise ErrorDePedido(status.HTTP_404_NOT_FOUND, f"No existe el producto {item.producto_id}")
            if producto["stock"] < item.cantidad:
                raise ErrorDePedido(
                    status.HTTP_409_CONFLICT,
                    f"Stock insuficiente de {item.producto_id}: "
                    f"hay {producto['stock']}, se pidieron {item.cantidad}",
                )
            contexto.productos[item.producto_id] = producto
