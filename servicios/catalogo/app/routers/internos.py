"""Endpoints internos: los usa el servicio de pedidos durante la SAGA.

  - POST /api/reservas                     → paso "reservar stock"
  - POST /api/reservas/{pedido_id}/liberar → compensación si un paso posterior falla

Los dos se pueden reintentar con el mismo pedido_id sin descontar ni devolver de más.
"""

from fastapi import APIRouter, Response, status

from app import servicio
from app.modelos import EstadoReserva, Reserva, ReservaCrear

router = APIRouter(prefix="/api/reservas", tags=["internos"])


@router.post("", response_model=Reserva)
async def reservar_stock(datos: ReservaCrear, response: Response):
    """200 si quedó reservado. 409 si no (el body dice el estado y el motivo).

    Un reintento de un pedido ya reservado vuelve a dar 200 con la misma reserva.
    """
    reserva = await servicio.reservar_stock(datos)
    if reserva.estado != EstadoReserva.RESERVADA:
        response.status_code = status.HTTP_409_CONFLICT
    return reserva


@router.post("/{pedido_id}/liberar", response_model=Reserva)
async def liberar_stock(pedido_id: str):
    """Siempre 200: después de llamarlo, el pedido no tiene stock retenido."""
    return await servicio.liberar_stock(pedido_id)
