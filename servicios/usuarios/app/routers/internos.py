"""Endpoints internos: no los usa el cliente final, sino otros microservicios.

El servicio de pedidos llama a este endpoint en el último paso de la SAGA,
cuando el pedido ya quedó confirmado.
"""

from fastapi import APIRouter, HTTPException, status

from app import servicio
from app.modelos import PedidoEnHistorial, Usuario

router = APIRouter(prefix="/api/usuarios", tags=["internos"])


@router.post("/{usuario_id}/historial", response_model=Usuario)
async def agregar_pedido_al_historial(usuario_id: str, datos: PedidoEnHistorial):
    try:
        return await servicio.agregar_pedido_al_historial(usuario_id, datos.pedido_id)
    except servicio.UsuarioNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")
