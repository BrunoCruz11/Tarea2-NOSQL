"""Clientes HTTP para consultar a los otros microservicios.

Pedidos no tiene acceso a las bases de usuarios ni de catálogo: todo lo que
necesita saber de ellos lo pregunta por su API.
"""

import httpx

from app import config


class ServicioNoDisponible(Exception):
    """El otro servicio no respondió, tardó demasiado o devolvió un error inesperado."""

    def __init__(self, servicio: str):
        self.servicio = servicio
        super().__init__(f"El servicio de {servicio} no está disponible")


_usuarios: httpx.AsyncClient | None = None
_catalogo: httpx.AsyncClient | None = None


def conectar() -> None:
    global _usuarios, _catalogo
    # Timeout corto: si un servicio está colgado preferimos responder 503 rápido
    # antes que dejar al cliente esperando.
    _usuarios = httpx.AsyncClient(base_url=config.USUARIOS_URL, timeout=3)
    _catalogo = httpx.AsyncClient(base_url=config.CATALOGO_URL, timeout=3)


async def desconectar() -> None:
    for cliente in (_usuarios, _catalogo):
        if cliente is not None:
            await cliente.aclose()


async def obtener_usuario(usuario_id: str) -> dict | None:
    """Devuelve el usuario, o None si no existe."""
    return await _obtener(_usuarios, f"/api/usuarios/{usuario_id}", "usuarios")


async def obtener_producto(producto_id: str) -> dict | None:
    """Devuelve el producto (con precio y stock), o None si no existe."""
    return await _obtener(_catalogo, f"/api/productos/{producto_id}", "catálogo")


async def reservar_stock(pedido_id: str, items: list[dict]) -> dict:
    """Pide al catálogo que reserve el stock. Devuelve la reserva (ver su `estado`).

    El catálogo responde 200 si reservó y 409 si no (con el motivo); los dos son
    respuestas válidas. Cualquier otra cosa es ServicioNoDisponible, y en ese caso
    no sabemos si llegó a reservar.
    """
    respuesta = await _post(
        _catalogo, "/api/reservas", {"pedido_id": pedido_id, "items": items}, "catálogo", (200, 409)
    )
    return respuesta.json()


async def liberar_stock(pedido_id: str) -> None:
    await _post(_catalogo, f"/api/reservas/{pedido_id}/liberar", None, "catálogo", (200,))


async def agregar_al_historial(usuario_id: str, pedido_id: str) -> bool:
    """Agrega el pedido al historial del usuario. Devuelve False si el usuario no existe."""
    respuesta = await _post(
        _usuarios, f"/api/usuarios/{usuario_id}/historial", {"pedido_id": pedido_id}, "usuarios", (200, 404)
    )
    return respuesta.status_code == 200


async def _obtener(cliente: httpx.AsyncClient, ruta: str, servicio: str) -> dict | None:
    try:
        respuesta = await cliente.get(ruta)
    except httpx.HTTPError as error:
        raise ServicioNoDisponible(servicio) from error

    if respuesta.status_code == 404:
        return None
    if respuesta.status_code != 200:
        raise ServicioNoDisponible(servicio)
    return respuesta.json()


async def _post(
    cliente: httpx.AsyncClient, ruta: str, body: dict | None, servicio: str, codigos_validos: tuple[int, ...]
) -> httpx.Response:
    try:
        respuesta = await cliente.post(ruta, json=body)
    except httpx.HTTPError as error:
        raise ServicioNoDisponible(servicio) from error

    if respuesta.status_code not in codigos_validos:
        raise ServicioNoDisponible(servicio)
    return respuesta
