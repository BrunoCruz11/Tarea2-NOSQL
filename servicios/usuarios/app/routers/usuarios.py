"""Endpoints públicos del servicio de usuarios (los que pide la letra).

Los routers solo traducen entre HTTP y el servicio: reciben el request ya validado
por Pydantic, llaman al servicio y convierten sus excepciones en códigos HTTP.
"""

from fastapi import APIRouter, HTTPException, Response, status

from app import servicio
from app.modelos import Usuario, UsuarioCrear
from app.repositorio import UsuarioDuplicado

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])


@router.post("", response_model=Usuario, status_code=status.HTTP_201_CREATED)
async def crear_usuario(datos: UsuarioCrear):
    try:
        return await servicio.crear_usuario(datos)
    except UsuarioDuplicado as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error))


@router.get("/{usuario_id}", response_model=Usuario)
async def obtener_usuario(usuario_id: str, response: Response):
    try:
        usuario, desde_cache = await servicio.obtener_usuario(usuario_id)
    except servicio.UsuarioNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado")

    # Header para ver en Postman si la respuesta salió de Redis (HIT) o de Mongo (MISS).
    response.headers["X-Cache"] = "HIT" if desde_cache else "MISS"
    return usuario
