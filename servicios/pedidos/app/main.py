from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import clientes, config, db, repositorio
from app.routers import pedidos


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conectar()
    clientes.conectar()
    await repositorio.crear_indices()
    yield
    db.desconectar()
    await clientes.desconectar()


app = FastAPI(title="ShopExpress - Servicio de Pedidos y Pagos", lifespan=lifespan)
app.include_router(pedidos.router)


@app.get("/health")
async def health():
    mongo = await db.estado()
    return JSONResponse(
        status_code=200 if mongo["ok"] else 503,
        content={"servicio": config.NOMBRE_SERVICIO, "estado": "ok" if mongo["ok"] else "degradado", "mongo": mongo},
    )
