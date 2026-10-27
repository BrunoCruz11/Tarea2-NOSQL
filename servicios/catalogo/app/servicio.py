"""Servicio: la lógica del negocio, entre los routers (HTTP) y el repositorio (Mongo).

Productos: cache-aside igual que en usuarios.

Reservas de stock (el paso de catálogo dentro de la SAGA de pedidos):

    pendiente ──► reservada ──► liberada      (pedidos compensa, p. ej. falló el pago)
        │
        ├──────► rechazada                    (faltó stock: se devuelve lo descontado)
        └──────► liberada                     (liberar llegó mientras se reservaba)

Reglas que hacen que el stock nunca se descuente ni se devuelva de más:
  1. El pedido_id es el `_id` de la reserva: un reintento no puede crear otra.
  2. Cada descuento de un producto es atómico y condicional (stock >= cantidad).
  3. Después de descontar un producto se anota en `descontados`. Quien saque la
     reserva de pendiente/reservada hacia rechazada o liberada (y como es atómico,
     es uno solo) devuelve exactamente lo que está anotado ahí.

No usamos una transacción multi-documento a propósito: con muchas compras a la vez
por el mismo producto, las transacciones chocan entre sí (WriteConflict) y hay que
reintentarlas; el update condicional resuelve la carrera en una sola operación.
El costo es que si el servicio se cae justo entre descontar y anotar, esa unidad
queda descontada sin estar en ninguna reserva. Elegimos ese orden porque el error
va para el lado seguro: se puede vender de menos, pero nunca de más.
"""

from app import cache, repositorio
from app.modelos import EstadoReserva, Producto, ProductoCrear, Reserva, ReservaCrear


class ProductoNoEncontrado(Exception):
    pass


def _clave_cache(producto_id: str) -> str:
    return f"producto:{producto_id}"


# ---------- Productos ----------

async def crear_producto(datos: ProductoCrear) -> Producto:
    return await repositorio.crear_producto(datos)


async def obtener_producto(producto_id: str) -> tuple[Producto, bool]:
    """Devuelve (producto, desde_cache). Cache-aside, igual que obtener_usuario.

    El stock que se muestra puede tener hasta CACHE_TTL_SEGUNDOS de atraso si una
    lectura se cruza con una compra. Para mostrarlo alcanza: la reserva no mira la caché.
    """
    clave = _clave_cache(producto_id)

    en_cache = await cache.leer(clave)
    if en_cache is not None:
        return Producto.model_validate(en_cache), True

    producto = await repositorio.obtener_producto(producto_id)
    if producto is None:
        raise ProductoNoEncontrado(producto_id)

    await cache.guardar(clave, producto.model_dump())
    return producto, False


# ---------- Reservas ----------

async def reservar_stock(datos: ReservaCrear) -> Reserva:
    """Descuenta el stock de todos los items del pedido, o de ninguno.

    Devuelve la reserva; su estado dice cómo terminó (reservada, rechazada, etc.).
    """
    try:
        await repositorio.crear_reserva(datos.pedido_id, datos.items)
    except repositorio.ReservaExistente:
        # Reintento del mismo pedido: no descontamos de nuevo, devolvemos cómo quedó.
        return await repositorio.obtener_reserva(datos.pedido_id)

    for item in datos.items:
        if not await repositorio.descontar_stock(item.producto_id, item.cantidad):
            if await repositorio.obtener_producto(item.producto_id) is None:
                motivo = f"No existe el producto {item.producto_id}"
            else:
                motivo = f"Stock insuficiente de {item.producto_id}"
            return await _cerrar_y_devolver(
                datos.pedido_id, [EstadoReserva.PENDIENTE], EstadoReserva.RECHAZADA, motivo
            )
        await cache.borrar(_clave_cache(item.producto_id))

        if not await repositorio.registrar_descuento(datos.pedido_id, item.producto_id):
            # Mientras reservábamos llegó un liberar (pedidos se cansó de esperar y
            # compensó). Lo que ya estaba anotado lo devolvió él; este último no
            # llegó a anotarse, así que lo devolvemos nosotros.
            await repositorio.devolver_stock(item.producto_id, item.cantidad)
            await cache.borrar(_clave_cache(item.producto_id))
            return await repositorio.obtener_reserva(datos.pedido_id)

    reservada = await repositorio.cambiar_estado(
        datos.pedido_id, [EstadoReserva.PENDIENTE], EstadoReserva.RESERVADA
    )
    # Si es None, la liberaron justo al final (y ya devolvieron todo).
    return reservada or await repositorio.obtener_reserva(datos.pedido_id)


async def liberar_stock(pedido_id: str) -> Reserva:
    """Compensación de la SAGA: devuelve el stock reservado para el pedido.

    Es idempotente: si ya estaba liberada (o rechazada) no devuelve nada otra vez.
    """
    while True:
        reserva = await _cerrar_y_devolver(
            pedido_id, [EstadoReserva.PENDIENTE, EstadoReserva.RESERVADA], EstadoReserva.LIBERADA
        )
        if reserva is not None and reserva.estado in (EstadoReserva.RECHAZADA, EstadoReserva.LIBERADA):
            return reserva

        if reserva is None and await repositorio.crear_reserva_liberada(pedido_id):
            return await repositorio.obtener_reserva(pedido_id)

        # Justo entró un reservar del mismo pedido entre una consulta y otra:
        # volvemos a intentar y esta vez lo liberamos.


async def _cerrar_y_devolver(
    pedido_id: str, desde: list[EstadoReserva], hacia: EstadoReserva, motivo: str | None = None
) -> Reserva | None:
    """Cierra la reserva y devuelve lo descontado. Si otro la cerró antes, no toca el stock."""
    cerrada = await repositorio.cambiar_estado(pedido_id, desde, hacia, motivo)
    if cerrada is None:
        return await repositorio.obtener_reserva(pedido_id)

    cantidades = {item.producto_id: item.cantidad for item in cerrada.items}
    for producto_id in cerrada.descontados:
        await repositorio.devolver_stock(producto_id, cantidades[producto_id])
        await cache.borrar(_clave_cache(producto_id))
    return cerrada
