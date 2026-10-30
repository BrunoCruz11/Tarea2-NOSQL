"""Prueba de concurrencia de punta a punta: muchos pedidos a la vez por POST /api/pedidos.

A diferencia de demo_sobreventa.py (que le pega directo a catálogo), esta pasa por
todo el sistema: la cadena de validaciones, la saga, la reserva en catálogo, el
pago y el historial en usuarios.

Con el docker compose levantado:

    pip install -r servicios/pedidos/tests/requirements.txt
    python scripts/prueba_concurrencia.py
    python scripts/prueba_concurrencia.py --stock 5 --pedidos 50

Hace dos pruebas:
  1. N clientes distintos compran 1 unidad a la vez de un producto con poco stock.
     Se tienen que confirmar exactamente `stock` pedidos, el stock tiene que quedar
     en 0 (nunca negativo) y el historial del usuario tener esos mismos pedidos.
  2. El mismo pedido (misma Idempotency-Key) se manda muchas veces a la vez.
     Se tiene que crear un solo pedido y descontar una sola unidad.
"""

import argparse
import asyncio
import os
import time
from collections import Counter
from uuid import uuid4

import httpx

USUARIOS_URL = os.environ.get("USUARIOS_URL", "http://localhost:8001")
CATALOGO_URL = os.environ.get("CATALOGO_URL", "http://localhost:8002")
PEDIDOS_URL = os.environ.get("PEDIDOS_URL", "http://localhost:8003")


async def crear_usuario(cliente: httpx.AsyncClient) -> dict:
    sufijo = uuid4().hex[:10]
    respuesta = await cliente.post(f"{USUARIOS_URL}/api/usuarios", json={
        "tipo_documento": "DNI",
        "nro_documento": sufijo,
        "nombre": "Compradora",
        "apellido": "Concurrente",
        "email": f"concurrencia.{sufijo}@example.com",
    })
    respuesta.raise_for_status()
    return respuesta.json()


async def crear_producto(cliente: httpx.AsyncClient, stock: int) -> dict:
    respuesta = await cliente.post(f"{CATALOGO_URL}/api/productos", json={
        "nombre": "Consola edición limitada", "categoria": "Videojuegos", "precio": 499.99, "stock": stock,
    })
    respuesta.raise_for_status()
    return respuesta.json()


async def leer_stock(cliente: httpx.AsyncClient, producto_id: str) -> int:
    respuesta = await cliente.get(f"{CATALOGO_URL}/api/productos/{producto_id}")
    respuesta.raise_for_status()
    return respuesta.json()["stock"]


async def leer_historial(cliente: httpx.AsyncClient, usuario_id: str) -> list[str]:
    respuesta = await cliente.get(f"{USUARIOS_URL}/api/usuarios/{usuario_id}")
    respuesta.raise_for_status()
    return respuesta.json()["historial_pedidos"]


async def pedir(
    cliente: httpx.AsyncClient, largada: asyncio.Event, clave: str, usuario_id: str, producto_id: str
) -> httpx.Response:
    await largada.wait()  # todos esperan acá y salen juntos
    return await cliente.post(
        f"{PEDIDOS_URL}/api/pedidos",
        headers={"Idempotency-Key": clave},
        json={
            "usuario_id": usuario_id,
            "items": [{"producto_id": producto_id, "cantidad": 1}],
            "metodo_pago": "TARJETA_CREDITO",
        },
    )


async def disparar_a_la_vez(
    cliente: httpx.AsyncClient, claves: list[str], usuario_id: str, producto_id: str
) -> tuple[list[httpx.Response], float]:
    largada = asyncio.Event()
    tareas = [asyncio.create_task(pedir(cliente, largada, c, usuario_id, producto_id)) for c in claves]
    await asyncio.sleep(0.2)  # que todas las tareas lleguen a la largada
    inicio = time.perf_counter()
    largada.set()
    respuestas = await asyncio.gather(*tareas)
    return respuestas, time.perf_counter() - inicio


def mostrar(titulo: str, ok: bool) -> bool:
    print(f"  {'OK   ' if ok else 'FALLA'}  {titulo}")
    return ok


async def prueba_muchos_pedidos(cliente: httpx.AsyncClient, stock: int, cantidad: int) -> bool:
    usuario = await crear_usuario(cliente)
    producto = await crear_producto(cliente, stock)
    print(f"\n1) {cantidad} pedidos distintos de 1 unidad a la vez, de {producto['id']} (stock: {stock})")

    claves = [f"concurrencia-{uuid4().hex}" for _ in range(cantidad)]
    respuestas, segundos = await disparar_a_la_vez(cliente, claves, usuario["id"], producto["id"])

    codigos = Counter(r.status_code for r in respuestas)
    confirmados = [r.json()["pedido_id"] for r in respuestas if r.status_code == 201]
    # Los 409 salen de dos lugares: ValidadorDeStock (ya vio el stock agotado, no
    # se crea pedido) o RESERVAR_STOCK en la saga (pasó la validación pero perdió
    # la reserva atómica: el pedido queda CANCELADO).
    cancelados = sum(1 for r in respuestas if r.status_code == 409 and "estado" in r.json())
    stock_final = await leer_stock(cliente, producto["id"])
    historial = await leer_historial(cliente, usuario["id"])

    print(f"  {cantidad} pedidos en {segundos:.2f}s")
    print(f"  Confirmados (201):                 {codigos[201]}")
    print(f"  Rechazados por stock (409):        {codigos[409]}")
    print(f"    - cortados en ValidadorDeStock:  {codigos[409] - cancelados}")
    print(f"    - CANCELADOS en RESERVAR_STOCK:  {cancelados}")
    otros = {c: n for c, n in codigos.items() if c not in (201, 409)}
    if otros:
        print(f"  Otros códigos:                     {otros}")
    print(f"  Stock final:                       {stock_final}")

    esperados = min(stock, cantidad)
    return all([
        mostrar(f"se confirmaron exactamente {esperados}", codigos[201] == esperados),
        mostrar("el resto se rechazó por falta de stock", codigos[409] == cantidad - esperados),
        mostrar(f"el stock quedó en {stock - esperados} (nunca negativo)", stock_final == stock - esperados),
        mostrar("el historial del usuario tiene justo los confirmados", sorted(historial) == sorted(confirmados)),
    ])


async def prueba_mismo_pedido_repetido(cliente: httpx.AsyncClient, repeticiones: int) -> bool:
    usuario = await crear_usuario(cliente)
    producto = await crear_producto(cliente, 10)
    clave = f"concurrencia-{uuid4().hex}"
    print(f"\n2) El mismo pedido (Idempotency-Key {clave[:22]}…) llega {repeticiones} veces a la vez (stock: 10)")

    respuestas, _ = await disparar_a_la_vez(cliente, [clave] * repeticiones, usuario["id"], producto["id"])

    codigos = Counter(r.status_code for r in respuestas)
    ids = {r.json()["pedido_id"] for r in respuestas if r.status_code in (200, 201)}
    stock_final = await leer_stock(cliente, producto["id"])
    historial = await leer_historial(cliente, usuario["id"])

    # 201 el que lo creó; 409 los que llegaron mientras todavía se procesaba;
    # 200 los que llegaron cuando ya había terminado (reciben el mismo pedido).
    print(f"  Respuestas: {dict(sorted(codigos.items()))}")
    print(f"  Stock final: {stock_final}")
    return all([
        mostrar("se creó un solo pedido", codigos[201] == 1 and len(ids) == 1),
        mostrar("se descontó una sola unidad", stock_final == 9),
        mostrar("el pedido figura una sola vez en el historial", historial == list(ids)),
    ])


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stock", type=int, default=5, help="stock inicial del producto (default: 5)")
    parser.add_argument("--pedidos", type=int, default=20, help="pedidos simultáneos (default: 20)")
    parser.add_argument("--repeticiones", type=int, default=20, help="reintentos del mismo pedido (default: 20)")
    args = parser.parse_args()

    # Sin límite de conexiones del lado del cliente, para que los requests salgan realmente juntos.
    limites = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(timeout=60, limits=limites) as cliente:
        print(f"Pedidos en {PEDIDOS_URL}")
        resultados = [
            await prueba_muchos_pedidos(cliente, args.stock, args.pedidos),
            await prueba_mismo_pedido_repetido(cliente, args.repeticiones),
        ]

    print("\nSin sobreventa y sin pedidos duplicados." if all(resultados) else "\nHubo diferencias, revisar arriba.")
    raise SystemExit(0 if all(resultados) else 1)


if __name__ == "__main__":
    asyncio.run(main())
