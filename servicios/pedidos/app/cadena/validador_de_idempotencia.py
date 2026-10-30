from fastapi import status

from app import repositorio
from app.cadena.base import ContextoPedido, ErrorDePedido, Manejador
from app.modelos import EstadoPedido, Pedido, PedidoCrear


class ValidadorDeIdempotencia(Manejador):
    """Garantiza que un pedido sea único: la misma Idempotency-Key no crea dos pedidos.

    El cliente manda un id propio por compra en el header Idempotency-Key. Si un
    request llega dos veces (doble clic, reintento por timeout), el segundo
    encuentra el pedido del primero y lo devuelve en vez de crear otro: la cadena
    termina acá y no se llama a la saga.

    Esta búsqueda es para responder rápido; la garantía la da el índice único sobre
    idempotency_key. Si dos requests iguales llegan a la vez y los dos pasan por acá
    sin ver nada, solo uno logra insertar el pedido (ver IniciadorDeSaga).
    """

    async def procesar(self, contexto: ContextoPedido) -> None:
        clave = contexto.idempotency_key
        if not clave:
            raise ErrorDePedido(status.HTTP_400_BAD_REQUEST, "Falta el header Idempotency-Key")
        if len(clave) > 100:
            raise ErrorDePedido(status.HTTP_400_BAD_REQUEST, "El header Idempotency-Key no puede pasar de 100 caracteres")

        existente = await repositorio.obtener_por_clave(clave)
        if existente is not None:
            responder_con_existente(contexto, existente)


def responder_con_existente(contexto: ContextoPedido, existente: Pedido) -> None:
    """Termina la cadena devolviendo el pedido que ya se creó con esa Idempotency-Key."""
    if not _mismo_pedido(contexto.datos, existente):
        raise ErrorDePedido(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Esa Idempotency-Key ya se usó para un pedido distinto; cada compra lleva una clave nueva",
        )
    if existente.estado in (EstadoPedido.PENDIENTE, EstadoPedido.PROCESANDO):
        # El primero todavía está corriendo la saga: el cliente puede consultarlo después.
        raise ErrorDePedido(
            status.HTTP_409_CONFLICT,
            f"El pedido {existente.pedido_id} con esa Idempotency-Key todavía se está procesando",
        )
    contexto.pedido = existente
    contexto.status_code = status.HTTP_200_OK  # 200 y no 201: no se creó nada nuevo
    contexto.terminado = True


def _mismo_pedido(datos: PedidoCrear, pedido: Pedido) -> bool:
    pedidos_items = sorted((item.producto_id, item.cantidad) for item in datos.items)
    guardados_items = sorted((linea.producto_id, linea.cantidad) for linea in pedido.items)
    return (
        datos.usuario_id == pedido.usuario_id
        and datos.metodo_pago == pedido.pago.metodo
        and pedidos_items == guardados_items
    )
