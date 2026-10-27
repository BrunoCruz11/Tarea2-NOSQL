"""Endpoints públicos del servicio de catálogo (los que pide la letra)."""

from fastapi import APIRouter, HTTPException, Response, status

from app import servicio
from app.modelos import Producto, ProductoCrear

router = APIRouter(prefix="/api/productos", tags=["productos"])


@router.post("", response_model=Producto, status_code=status.HTTP_201_CREATED)
async def crear_producto(datos: ProductoCrear):
    return await servicio.crear_producto(datos)


@router.get("/{producto_id}", response_model=Producto)
async def obtener_producto(producto_id: str, response: Response):
    try:
        producto, desde_cache = await servicio.obtener_producto(producto_id)
    except servicio.ProductoNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")

    # Header para ver en Postman si la respuesta salió de Redis (HIT) o de Mongo (MISS).
    response.headers["X-Cache"] = "HIT" if desde_cache else "MISS"
    return producto
