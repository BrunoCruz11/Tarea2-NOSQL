from fastapi import status
from pydantic import ValidationError

from app.cadena.base import ContextoPedido, ErrorDePedido, Manejador
from app.modelos import PedidoCrear


class ValidadorDeDatos(Manejador):
    """Revisa que el body tenga la forma de un pedido: usuario_id e items válidos.

    Es el único lugar donde se valida el formato: el router recibe el JSON crudo
    para que esta verificación sea el primer eslabón de la cadena. Responde 422
    con el mismo formato de errores que usa FastAPI.
    """

    async def procesar(self, contexto: ContextoPedido) -> None:
        try:
            contexto.datos = PedidoCrear.model_validate(contexto.body)
        except ValidationError as error:
            raise ErrorDePedido(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                error.errors(include_url=False, include_context=False),
            ) from error
