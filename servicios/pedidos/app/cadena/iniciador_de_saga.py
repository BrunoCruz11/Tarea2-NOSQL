from app import repositorio, saga
from app.cadena.base import ContextoPedido, Manejador
from app.cadena.validador_de_idempotencia import responder_con_existente


class IniciadorDeSaga(Manejador):
    """Último eslabón: el pedido ya pasó todas las validaciones, arranca la saga.

    Acá ya no se corta con ErrorDePedido: si la saga falla, el pedido igual quedó
    creado (CANCELADO) y se devuelve con su historial para ver qué pasó.
    """

    async def procesar(self, contexto: ContextoPedido) -> None:
        try:
            contexto.pedido, contexto.status_code = await saga.ejecutar(contexto)
        except repositorio.PedidoDuplicado:
            # Otro request con la misma Idempotency-Key insertó su pedido primero
            # (pasaron los dos por ValidadorDeIdempotencia al mismo tiempo). El
            # índice único rechazó el nuestro antes de hacer nada: devolvemos el suyo.
            existente = await repositorio.obtener_por_clave(contexto.idempotency_key)
            responder_con_existente(contexto, existente)
