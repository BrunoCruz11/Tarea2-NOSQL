"""Base de la Chain of Responsibility para crear pedidos.

Cada manejador hace una sola verificación (o un solo cálculo) y, si sale bien,
le pasa el contexto al siguiente. Si algo falla levanta ErrorDePedido: la cadena
se corta ahí y los manejadores que siguen no se ejecutan.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.modelos import LineaPedido, Pedido, PedidoCrear


class ErrorDePedido(Exception):
    """Un manejador rechazó el pedido. El router lo traduce a una respuesta HTTP."""

    def __init__(self, status_code: int, detalle: Any):
        self.status_code = status_code
        self.detalle = detalle
        super().__init__(detalle)


@dataclass
class ContextoPedido:
    """Lo que va pasando de un manejador al siguiente. Cada uno completa su parte."""

    body: Any  # el JSON tal como llegó, sin validar
    datos: PedidoCrear | None = None  # ValidadorDeDatos
    usuario: dict | None = None  # ValidadorDeUsuario
    productos: dict[str, dict] = field(default_factory=dict)  # ValidadorDeStock, por producto_id
    lineas: list[LineaPedido] = field(default_factory=list)  # CalculadorDeTotal
    total: float | None = None  # CalculadorDeTotal
    pedido: Pedido | None = None  # IniciadorDeSaga
    status_code: int | None = None  # IniciadorDeSaga: 201 si se confirmó, o el error del paso que falló


class Manejador(ABC):
    def __init__(self) -> None:
        self._siguiente: Manejador | None = None

    def enlazar(self, siguiente: "Manejador") -> "Manejador":
        """Engancha el siguiente eslabón. Devuelve ese mismo, para encadenar: a.enlazar(b).enlazar(c)."""
        self._siguiente = siguiente
        return siguiente

    async def manejar(self, contexto: ContextoPedido) -> None:
        await self.procesar(contexto)
        if self._siguiente is not None:
            await self._siguiente.manejar(contexto)

    @abstractmethod
    async def procesar(self, contexto: ContextoPedido) -> None:
        """Hace el trabajo de este eslabón. Levanta ErrorDePedido para cortar la cadena."""
