"""Endpoints públicos del servicio de usuarios (los que pide la letra).

Los routers solo traducen entre HTTP y el servicio: reciben el request ya validado
por Pydantic, llaman al servicio y convierten sus excepciones en códigos HTTP.
"""

import csv
import io
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status

from app import servicio
from app.modelos import Usuario, UsuarioAnonimo, UsuarioCrear
from app.repositorio import UsuarioDuplicado

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])

_COLUMNAS_CSV = [
    "tipo_documento", "documento_hash", "email_hash", "nombre", "apellido", "direcciones", "cantidad_pedidos",
]


@router.post("", response_model=Usuario, status_code=status.HTTP_201_CREATED)
async def crear_usuario(datos: UsuarioCrear):
    try:
        return await servicio.crear_usuario(datos)
    except UsuarioDuplicado as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error))


# Tiene que ir antes que GET /{usuario_id}: si no, FastAPI toma "exportar" como un id.
@router.get("/exportar", response_model=list[UsuarioAnonimo])
async def exportar_usuarios(formato: Literal["json", "csv"] = "json"):
    """Todos los usuarios con los datos personales seudonimizados. `?formato=csv` lo baja como archivo."""
    usuarios = await servicio.exportar_usuarios()
    if formato == "json":
        return usuarios
    return Response(
        content=_a_csv(usuarios),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="usuarios.csv"'},
    )


@router.get("/{usuario_id}", response_model=Usuario)
async def obtener_usuario(usuario_id: str, response: Response):
    try:
        usuario, desde_cache = await servicio.obtener_usuario(usuario_id)
    except servicio.UsuarioNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    # Header para ver en Postman si la respuesta salió de Redis (HIT) o de Mongo (MISS).
    response.headers["X-Cache"] = "HIT" if desde_cache else "MISS"
    return usuario


def _a_csv(usuarios: list[UsuarioAnonimo]) -> str:
    # Un CSV no tiene listas: las direcciones van en una sola celda, "ciudad (cp); ciudad (cp)".
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(_COLUMNAS_CSV)
    for usuario in usuarios:
        escritor.writerow([
            usuario.tipo_documento,
            usuario.documento_hash,
            usuario.email_hash,
            usuario.nombre,
            usuario.apellido,
            "; ".join(f"{d.ciudad} ({d.codigo_postal})" for d in usuario.direcciones),
            usuario.cantidad_pedidos,
        ])
    return salida.getvalue()
