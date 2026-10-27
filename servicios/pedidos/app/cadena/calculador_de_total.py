from decimal import ROUND_HALF_UP, Decimal

from app.cadena.base import ContextoPedido, Manejador
from app.modelos import LineaPedido

CENTAVOS = Decimal("0.01")


class CalculadorDeTotal(Manejador):
    """Arma las líneas del pedido y el total con los precios que devolvió el catálogo.

    Nunca se usan precios que mande el cliente (PedidoCrear ni siquiera los tiene).
    Las cuentas se hacen con Decimal para evitar errores de redondeo de float
    (por ejemplo 0.1 + 0.2 = 0.30000000000000004).
    """

    async def procesar(self, contexto: ContextoPedido) -> None:
        total = Decimal(0)
        for item in contexto.datos.items:
            producto = contexto.productos[item.producto_id]
            precio = Decimal(str(producto["precio"]))
            total += (precio * item.cantidad).quantize(CENTAVOS, ROUND_HALF_UP)
            contexto.lineas.append(LineaPedido(
                producto_id=item.producto_id,
                cantidad=item.cantidad,
                precio_unitario=float(precio),
            ))
        contexto.total = float(total)
