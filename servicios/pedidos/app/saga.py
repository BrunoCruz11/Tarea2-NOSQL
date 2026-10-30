"""SAGA orquestada de creación de pedidos. Pedidos es el orquestador central.

    Paso                  Si falla un paso posterior, se compensa con
    1. CREAR_PEDIDO       CANCELAR_PEDIDO   (estado CANCELADO)
    2. RESERVAR_STOCK     LIBERAR_STOCK     (catálogo devuelve el stock)
    3. PROCESAR_PAGO      REEMBOLSAR_PAGO   (pasarela devuelve el dinero)
    4. CONFIRMAR_PEDIDO   (último paso: si sale bien la saga terminó)

Si el paso N falla, se ejecutan las compensaciones de los pasos N-1 ... 1, en
ese orden (de atrás para adelante). Cada paso y cada compensación queda anotado
en el historial del pedido, junto con el cambio de estado que produjo.

Las compensaciones de catálogo se pueden repetir sin problema (liberar es
idempotente), por eso se reintentan si el servicio no responde.

Limitación: el progreso de la saga se guarda en Mongo, pero si el servicio de
pedidos se cae a mitad de una saga, nadie la retoma al volver a levantar; quedaría
el pedido en PENDIENTE o PROCESANDO. Para eso haría falta un proceso que busque
sagas sin terminar y las compense.
"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import status

from app import clientes, pagos, repositorio
from app.cadena.base import ContextoPedido
from app.modelos import EstadoPago, EstadoPedido, Pago, PasoHistorial, Pedido, ResultadoPaso

log = logging.getLogger(__name__)


class FalloDePaso(Exception):
    """Un paso no se pudo completar.

    - status_code: con qué código HTTP responde POST /api/pedidos.
    - cambios: lo que hay que guardar igual en el pedido (p. ej. el pago RECHAZADO).
    - incierto: no sabemos si el otro servicio llegó a hacerlo (timeout, caída).
      En ese caso también se compensa el paso que falló, por las dudas.
    """

    def __init__(self, motivo: str, status_code: int, cambios: dict | None = None, incierto: bool = False):
        self.motivo = motivo
        self.status_code = status_code
        self.cambios = cambios or {}
        self.incierto = incierto
        super().__init__(motivo)


class Paso:
    """Un paso de la saga. Cada método devuelve (detalle para el historial, cambios en el pedido)."""

    nombre: str
    compensacion: str | None = None  # nombre de la compensación; None si no tiene

    async def ejecutar(self, pedido: Pedido) -> tuple[str, dict]:
        raise NotImplementedError

    async def compensar(self, pedido: Pedido) -> tuple[str, dict]:
        raise NotImplementedError


class CrearPedido(Paso):
    nombre = "CREAR_PEDIDO"
    compensacion = "CANCELAR_PEDIDO"

    async def ejecutar(self, pedido):
        await repositorio.crear(pedido)
        # Los datos iniciales van como cambios: en el pedido no modifican nada (ya
        # están), pero quedan en el evento, y con eso los eventos alcanzan para
        # reconstruir el pedido entero.
        datos_iniciales = pedido.model_dump(mode="json", include={"usuario_id", "estado", "items", "total", "pago"})
        return "Pedido creado como PENDIENTE", datos_iniciales

    async def compensar(self, pedido):
        return "Pedido cancelado", {"estado": EstadoPedido.CANCELADO}


class ReservarStock(Paso):
    nombre = "RESERVAR_STOCK"
    compensacion = "LIBERAR_STOCK"

    async def ejecutar(self, pedido):
        items = [{"producto_id": linea.producto_id, "cantidad": linea.cantidad} for linea in pedido.items]
        try:
            reserva = await clientes.reservar_stock(pedido.pedido_id, items)
        except clientes.ServicioNoDisponible as error:
            # Puede que catálogo haya reservado y no nos llegó la respuesta.
            raise FalloDePaso(str(error), status.HTTP_503_SERVICE_UNAVAILABLE, incierto=True) from error

        if reserva["estado"] != "reservada":
            # Catálogo ya devolvió lo que había descontado: no hace falta liberar.
            motivo = reserva.get("motivo") or f"La reserva quedó {reserva['estado']}"
            raise FalloDePaso(motivo, status.HTTP_409_CONFLICT)
        return "Stock reservado en catálogo", {"estado": EstadoPedido.PROCESANDO}

    async def compensar(self, pedido):
        await _con_reintentos(lambda: clientes.liberar_stock(pedido.pedido_id))
        return "Stock devuelto al catálogo", {}


class ProcesarPago(Paso):
    nombre = "PROCESAR_PAGO"
    compensacion = "REEMBOLSAR_PAGO"

    async def ejecutar(self, pedido):
        try:
            transaccion_id = await pagos.cobrar(pedido.total, pedido.pago.metodo)
        except pagos.PagoRechazado as error:
            raise FalloDePaso(
                str(error), status.HTTP_402_PAYMENT_REQUIRED, cambios={"pago.estado_pago": EstadoPago.RECHAZADO}
            ) from error
        return (
            f"Pago aprobado, transacción {transaccion_id}",
            {"pago.estado_pago": EstadoPago.APROBADO, "pago.transaccion_id": transaccion_id},
        )

    async def compensar(self, pedido):
        await pagos.reembolsar(pedido.pago.transaccion_id)
        return (
            f"Reembolsada la transacción {pedido.pago.transaccion_id}",
            {"pago.estado_pago": EstadoPago.REEMBOLSADO},
        )


class ConfirmarPedido(Paso):
    nombre = "CONFIRMAR_PEDIDO"

    async def ejecutar(self, pedido):
        # No tiene compensación: usuarios no permite sacar un pedido del historial.
        # Si usuarios no responde puede que lo haya agregado igual; en ese caso el
        # historial del usuario muestra un pedido que acá figura como CANCELADO.
        try:
            existe = await clientes.agregar_al_historial(pedido.usuario_id, pedido.pedido_id)
        except clientes.ServicioNoDisponible as error:
            raise FalloDePaso(str(error), status.HTTP_503_SERVICE_UNAVAILABLE) from error
        if not existe:
            raise FalloDePaso(f"No existe el usuario {pedido.usuario_id}", status.HTTP_404_NOT_FOUND)
        return "Pedido confirmado y agregado al historial del usuario", {"estado": EstadoPedido.CONFIRMADO}


PASOS: list[Paso] = [CrearPedido(), ReservarStock(), ProcesarPago(), ConfirmarPedido()]


async def ejecutar(contexto: ContextoPedido) -> tuple[Pedido, int]:
    """Corre la saga. Devuelve el pedido final (CONFIRMADO o CANCELADO) y el código HTTP."""
    pedido = Pedido(
        pedido_id=f"ord_{uuid4().hex[:12]}",
        usuario_id=contexto.datos.usuario_id,
        estado=EstadoPedido.PENDIENTE,
        items=contexto.lineas,
        total=contexto.total,
        pago=Pago(metodo=contexto.datos.metodo_pago, estado_pago=EstadoPago.PENDIENTE),
        idempotency_key=contexto.idempotency_key,
    )

    hechos: list[Paso] = []
    for paso in PASOS:
        try:
            detalle, cambios = await paso.ejecutar(pedido)
        except repositorio.PedidoDuplicado:
            # El pedido no llegó a crearse (ya había otro con esa Idempotency-Key):
            # no hay nada que registrar ni compensar.
            raise
        except Exception as error:
            fallo = error if isinstance(error, FalloDePaso) else _fallo_inesperado(paso, error)
            pedido = await _registrar(pedido, paso.nombre, ResultadoPaso.FALLO, fallo.motivo, fallo.cambios)
            if fallo.incierto:
                hechos.append(paso)
            return await _compensar(pedido, hechos), fallo.status_code

        pedido = await _registrar(pedido, paso.nombre, ResultadoPaso.OK, detalle, cambios)
        hechos.append(paso)

    return pedido, status.HTTP_201_CREATED


async def _compensar(pedido: Pedido, hechos: list[Paso]) -> Pedido:
    for paso in reversed(hechos):
        if paso.compensacion is None:
            continue
        try:
            detalle, cambios = await paso.compensar(pedido)
            resultado = ResultadoPaso.OK
        except Exception as error:
            # Seguimos con las demás: que falle una no es motivo para dejar las otras sin hacer.
            log.exception("Falló %s del pedido %s", paso.compensacion, pedido.pedido_id)
            detalle, cambios, resultado = f"No se pudo compensar: {error}", {}, ResultadoPaso.FALLO
        pedido = await _registrar(pedido, paso.compensacion, resultado, detalle, cambios)
    return pedido


async def _registrar(pedido: Pedido, paso: str, resultado: ResultadoPaso, detalle: str, cambios: dict) -> Pedido:
    entrada = PasoHistorial(paso=paso, resultado=resultado, detalle=detalle, fecha=datetime.now(timezone.utc))
    return await repositorio.registrar_paso(pedido.pedido_id, entrada, cambios)


def _fallo_inesperado(paso: Paso, error: Exception) -> FalloDePaso:
    # Un error que no esperábamos (un bug, Mongo caído...): no sabemos qué quedó hecho,
    # así que lo tratamos como incierto y compensamos también ese paso.
    log.exception("Error inesperado en %s", paso.nombre, exc_info=error)
    return FalloDePaso(f"Error inesperado: {error}", status.HTTP_500_INTERNAL_SERVER_ERROR, incierto=True)


async def _con_reintentos(operacion, intentos: int = 3):
    for intento in range(1, intentos + 1):
        try:
            return await operacion()
        except clientes.ServicioNoDisponible:
            if intento == intentos:
                raise
            await asyncio.sleep(0.2 * intento)
