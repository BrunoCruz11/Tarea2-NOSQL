from app import saga
from app.cadena.base import ContextoPedido, Manejador


class IniciadorDeSaga(Manejador):
    """Último eslabón: el pedido ya pasó todas las validaciones, arranca la saga.

    Acá ya no se corta con ErrorDePedido: si la saga falla, el pedido igual quedó
    creado (CANCELADO) y se devuelve con su historial para ver qué pasó.
    """

    async def procesar(self, contexto: ContextoPedido) -> None:
        contexto.pedido, contexto.status_code = await saga.ejecutar(contexto)
