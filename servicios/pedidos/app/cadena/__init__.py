"""Cadena de manejadores para POST /api/pedidos, en el orden en que se ejecutan."""

from app.cadena.base import ContextoPedido, ErrorDePedido, Manejador
from app.cadena.calculador_de_total import CalculadorDeTotal
from app.cadena.iniciador_de_saga import IniciadorDeSaga
from app.cadena.validador_de_datos import ValidadorDeDatos
from app.cadena.validador_de_idempotencia import ValidadorDeIdempotencia
from app.cadena.validador_de_stock import ValidadorDeStock
from app.cadena.validador_de_usuario import ValidadorDeUsuario

__all__ = ["ContextoPedido", "ErrorDePedido", "construir_cadena"]


def construir_cadena() -> Manejador:
    # Los manejadores no guardan estado entre requests, así que la cadena se arma una
    # sola vez y se reusa; lo de cada pedido viaja en el ContextoPedido.
    inicio = ValidadorDeDatos()
    (
        # Idempotencia antes que usuario y stock: un pedido repetido se resuelve
        # sin volver a consultar a los otros servicios.
        inicio.enlazar(ValidadorDeIdempotencia())
        .enlazar(ValidadorDeUsuario())
        .enlazar(ValidadorDeStock())
        .enlazar(CalculadorDeTotal())
        .enlazar(IniciadorDeSaga())
    )
    return inicio
