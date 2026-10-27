from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import cache, config, db
from app.routers import internos, productos


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conectar()
    cache.conectar()
    yield
    db.desconectar()
    await cache.desconectar()


app = FastAPI(title="ShopExpress - Servicio de Catálogo e Inventario", lifespan=lifespan)
app.include_router(productos.router)
app.include_router(internos.router)


@app.get("/health")
async def health():
    dependencias = {"mongo": await db.estado(), "redis": await cache.estado()}
    sano = all(d["ok"] for d in dependencias.values())
    return JSONResponse(
        status_code=200 if sano else 503,
        content={"servicio": config.NOMBRE_SERVICIO, "estado": "ok" if sano else "degradado", **dependencias},
    )
