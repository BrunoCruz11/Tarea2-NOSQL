from fastapi import status

from app import clientes
from app.cadena.base import ContextoPedido, ErrorDePedido, Manejador


class ValidadorDeUsuario(Manejador):
    """Le pregunta al servicio de usuarios si el usuario existe."""

    async def procesar(self, contexto: ContextoPedido) -> None:
        try:
            usuario = await clientes.obtener_usuario(contexto.datos.usuario_id)
        except clientes.ServicioNoDisponible as error:
            raise ErrorDePedido(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

        if usuario is None:
            raise ErrorDePedido(
                status.HTTP_404_NOT_FOUND, f"No existe el usuario {contexto.datos.usuario_id}"
            )
        contexto.usuario = usuario
