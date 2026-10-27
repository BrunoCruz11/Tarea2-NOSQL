"""Endpoints públicos del servicio de pedidos."""

from fastapi import APIRouter, HTTPException, Request, Response, status

from app import repositorio
from app.cadena import ContextoPedido, ErrorDePedido, construir_cadena
from app.modelos import Pedido

router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])

_cadena = construir_cadena()

# El body se recibe crudo (lo valida ValidadorDeDatos), así que FastAPI no lo
# documenta solo; le pasamos un ejemplo para que aparezca en /docs.
_EJEMPLO_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"example": {
            "usuario_id": "usr_3f9a1c2b7d4e",
            "items": [{"producto_id": "prod_3f9a1c2b7d4e", "cantidad": 2}],
            "metodo_pago": "TARJETA_CREDITO",
        }}},
    }
}


@router.post("", response_model=Pedido, status_code=status.HTTP_201_CREATED, openapi_extra=_EJEMPLO_BODY)
async def crear_pedido(request: Request, response: Response):
    """Corre la cadena de validaciones y la saga.

    - Si una validación falla: el error correspondiente y no se crea el pedido.
    - Si la saga sale bien: 201 con el pedido CONFIRMADO.
    - Si la saga falla: el pedido CANCELADO con su historial, y el código del paso
      que falló (409 sin stock, 402 pago rechazado, 503 servicio caído).
    """
    try:
        body = await request.json()
    except ValueError:
        body = None  # JSON mal formado: ValidadorDeDatos lo rechaza con 422

    contexto = ContextoPedido(body=body)
    try:
        await _cadena.manejar(contexto)
    except ErrorDePedido as error:
        raise HTTPException(error.status_code, detail=error.detalle)

    response.status_code = contexto.status_code
    return contexto.pedido


@router.get("/{pedido_id}", response_model=Pedido)
async def obtener_pedido(pedido_id: str):
    # Sin caché a propósito: el estado de un pedido tiene que leerse siempre actualizado.
    pedido = await repositorio.obtener(pedido_id)
    if pedido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")
    return pedido
