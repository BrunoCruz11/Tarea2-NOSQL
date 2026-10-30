"""Servicio: la lógica del negocio, entre los routers (HTTP) y el repositorio (Mongo).

Acá vive el patrón cache-aside: el repositorio no sabe que existe Redis y los
routers no saben de dónde salió el dato.
"""

from app import anonimizacion, cache, repositorio
from app.modelos import Usuario, UsuarioAnonimo, UsuarioCrear


class UsuarioNoEncontrado(Exception):
    pass


def _clave_cache(usuario_id: str) -> str:
    return f"usuario:{usuario_id}"


async def crear_usuario(datos: UsuarioCrear) -> Usuario:
    # No lo guardamos en caché al crearlo: entra recién cuando alguien lo lee.
    return await repositorio.crear(datos)


async def obtener_usuario(usuario_id: str) -> tuple[Usuario, bool]:
    """Devuelve (usuario, desde_cache).

    Cache-aside:
      1. Buscar en Redis. Si está (HIT), devolverlo sin tocar Mongo.
      2. Si no está (MISS), leer de Mongo y guardarlo en Redis con TTL
         para que las próximas lecturas sean HIT.
    """
    clave = _clave_cache(usuario_id)

    en_cache = await cache.leer(clave)
    if en_cache is not None:
        return Usuario.model_validate(en_cache), True

    usuario = await repositorio.obtener(usuario_id)
    if usuario is None:
        raise UsuarioNoEncontrado(usuario_id)

    await cache.guardar(clave, usuario.model_dump())
    return usuario, False


async def agregar_pedido_al_historial(usuario_id: str, pedido_id: str) -> Usuario:
    usuario = await repositorio.agregar_pedido(usuario_id, pedido_id)
    if usuario is None:
        raise UsuarioNoEncontrado(usuario_id)

    # Escribimos en Mongo y después invalidamos la caché: la próxima lectura
    # va a ser MISS y trae el historial actualizado.
    await cache.borrar(_clave_cache(usuario_id))
    return usuario


async def exportar_usuarios() -> list[UsuarioAnonimo]:
    """Todos los usuarios, seudonimizados.

    Lee directo de Mongo y no pasa por la caché: es un volcado completo, no una
    lectura de un usuario puntual.
    """
    return [anonimizacion.anonimizar(usuario) async for usuario in repositorio.listar()]
