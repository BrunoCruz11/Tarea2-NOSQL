"""Demo: muchas compras simultáneas de un producto con poco stock, sin sobreventa.

Con el docker compose levantado:

    pip install -r servicios/catalogo/tests/requirements.txt
    python scripts/demo_sobreventa.py
    python scripts/demo_sobreventa.py --stock 3 --compradores 500

Hace dos pruebas contra el servicio de catálogo:
  1. N compradores distintos piden 1 unidad a la vez. Tienen que venderse
     exactamente `stock` unidades y el stock tiene que terminar en 0, no negativo.
  2. El mismo pedido se manda muchas veces a la vez (reintentos). El stock tiene
     que bajar una sola vez.
"""

import argparse
import asyncio
import os
import time
from collections import Counter
from uuid import uuid4

import httpx

URL = os.environ.get("CATALOGO_URL", "http://localhost:8002")


async def crear_producto(cliente: httpx.AsyncClient, stock: int) -> dict:
    respuesta = await cliente.post(
        "/api/productos", json={"nombre": "Consola edición limitada", "precio": 499.99, "stock": stock}
    )
    respuesta.raise_for_status()
    return respuesta.json()


async def leer_stock(cliente: httpx.AsyncClient, producto_id: str) -> int:
    respuesta = await cliente.get(f"/api/productos/{producto_id}")
    respuesta.raise_for_status()
    return respuesta.json()["stock"]


async def comprar(cliente: httpx.AsyncClient, largada: asyncio.Event, pedido_id: str, producto_id: str) -> int:
    await largada.wait()  # todos esperan acá y salen juntos
    respuesta = await cliente.post(
        "/api/reservas",
        json={"pedido_id": pedido_id, "items": [{"producto_id": producto_id, "cantidad": 1}]},
    )
    return respuesta.status_code


async def disparar_a_la_vez(cliente: httpx.AsyncClient, pedidos: list[str], producto_id: str) -> tuple[Counter, float]:
    largada = asyncio.Event()
    tareas = [asyncio.create_task(comprar(cliente, largada, p, producto_id)) for p in pedidos]
    await asyncio.sleep(0.2)  # que todas las tareas lleguen a la largada
    inicio = time.perf_counter()
    largada.set()
    codigos = await asyncio.gather(*tareas)
    return Counter(codigos), time.perf_counter() - inicio


def mostrar(titulo: str, ok: bool) -> bool:
    print(f"  {'OK   ' if ok else 'FALLA'}  {titulo}")
    return ok


async def prueba_muchos_compradores(cliente: httpx.AsyncClient, stock: int, compradores: int) -> bool:
    producto = await crear_producto(cliente, stock)
    print(f"\n1) {compradores} compradores distintos quieren 1 unidad de {producto['id']} (stock: {stock})")

    pedidos = [f"ord_demo_{uuid4().hex[:10]}" for _ in range(compradores)]
    codigos, segundos = await disparar_a_la_vez(cliente, pedidos, producto["id"])
    stock_final = await leer_stock(cliente, producto["id"])

    print(f"  {compradores} requests en {segundos:.2f}s")
    print(f"  Compras aceptadas (200):    {codigos[200]}")
    print(f"  Compras rechazadas (409):   {codigos[409]}")
    otros = {c: n for c, n in codigos.items() if c not in (200, 409)}
    if otros:
        print(f"  Otros códigos:              {otros}")
    print(f"  Stock final:                {stock_final}")

    vendidas_esperadas = min(stock, compradores)
    return all([
        mostrar(f"se vendieron exactamente {vendidas_esperadas}", codigos[200] == vendidas_esperadas),
        mostrar("el resto se rechazó", codigos[409] == compradores - vendidas_esperadas),
        mostrar(f"el stock quedó en {stock - vendidas_esperadas} (nunca negativo)", stock_final == stock - vendidas_esperadas),
    ])


async def prueba_mismo_pedido_repetido(cliente: httpx.AsyncClient, repeticiones: int) -> bool:
    producto = await crear_producto(cliente, 10)
    pedido_id = f"ord_demo_{uuid4().hex[:10]}"
    print(f"\n2) El mismo pedido {pedido_id} llega {repeticiones} veces a la vez (stock: 10)")

    codigos, _ = await disparar_a_la_vez(cliente, [pedido_id] * repeticiones, producto["id"])
    stock_final = await leer_stock(cliente, producto["id"])

    # Los que llegan mientras el primero todavía está reservando reciben 409 (en curso);
    # los que llegan después reciben 200 con la misma reserva. Ninguno descuenta de nuevo.
    print(f"  Respuestas: {dict(codigos)}")
    print(f"  Stock final: {stock_final}")
    return mostrar("se descontó una sola unidad", stock_final == 9)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stock", type=int, default=5, help="stock inicial del producto (default: 5)")
    parser.add_argument("--compradores", type=int, default=200, help="compras simultáneas (default: 200)")
    parser.add_argument("--repeticiones", type=int, default=50, help="reintentos del mismo pedido (default: 50)")
    args = parser.parse_args()

    # Sin límite de conexiones del lado del cliente, para que los requests salgan realmente juntos.
    limites = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(base_url=URL, timeout=30, limits=limites) as cliente:
        print(f"Catálogo en {URL}")
        resultados = [
            await prueba_muchos_compradores(cliente, args.stock, args.compradores),
            await prueba_mismo_pedido_repetido(cliente, args.repeticiones),
        ]

    print("\nSin sobreventa." if all(resultados) else "\nHubo diferencias, revisar arriba.")
    raise SystemExit(0 if all(resultados) else 1)


if __name__ == "__main__":
    asyncio.run(main())
