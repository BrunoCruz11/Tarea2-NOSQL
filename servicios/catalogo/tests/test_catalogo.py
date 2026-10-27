"""Tests de punta a punta contra el servicio levantado con docker compose.

    pip install -r servicios/catalogo/tests/requirements.txt
    pytest servicios/catalogo/tests

Cada test crea sus propios productos y usa pedido_id al azar, así se pueden
correr muchas veces sin borrar la base.
"""

import os
from uuid import uuid4

import httpx
import pytest

URL = os.environ.get("CATALOGO_URL", "http://localhost:8002")


@pytest.fixture
def cliente():
    with httpx.Client(base_url=URL, timeout=10) as cliente:
        yield cliente


def crear_producto(cliente, **cambios) -> dict:
    datos = {"nombre": "Auriculares", "descripcion": "Bluetooth", "precio": 59.99, "stock": 10, **cambios}
    respuesta = cliente.post("/api/productos", json=datos)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def stock_actual(cliente, producto_id: str) -> int:
    # Después de cada cambio de stock la caché se invalida, así que esto lee lo último.
    return cliente.get(f"/api/productos/{producto_id}").json()["stock"]


def nuevo_pedido_id() -> str:
    return f"ord_{uuid4().hex[:10]}"


def reservar(cliente, pedido_id: str, *items: tuple[str, int]):
    return cliente.post(
        "/api/reservas",
        json={"pedido_id": pedido_id, "items": [{"producto_id": p, "cantidad": c} for p, c in items]},
    )


# ---------- POST /api/productos ----------

def test_crear_producto_devuelve_id_con_prefijo(cliente):
    producto = crear_producto(cliente, stock=7)

    assert producto["id"].startswith("prod_")
    assert producto["stock"] == 7


@pytest.mark.parametrize("cambios", [{"nombre": ""}, {"precio": 0}, {"stock": -1}, {"stock": 1.5}])
def test_datos_invalidos_dan_422(cliente, cambios):
    datos = {"nombre": "Auriculares", "precio": 10, "stock": 1, **cambios}

    assert cliente.post("/api/productos", json=datos).status_code == 422


# ---------- GET /api/productos/{id} ----------

def test_obtener_producto_primero_de_mongo_y_despues_de_redis(cliente):
    producto = crear_producto(cliente)

    primera = cliente.get(f"/api/productos/{producto['id']}")
    segunda = cliente.get(f"/api/productos/{producto['id']}")

    assert primera.headers["X-Cache"] == "MISS"
    assert segunda.headers["X-Cache"] == "HIT"
    assert primera.json() == segunda.json() == producto


def test_producto_inexistente_da_404(cliente):
    assert cliente.get("/api/productos/prod_noexiste").status_code == 404


def test_reservar_invalida_la_cache_y_se_ve_el_stock_nuevo(cliente):
    producto = crear_producto(cliente, stock=10)
    cliente.get(f"/api/productos/{producto['id']}")  # queda en caché

    reservar(cliente, nuevo_pedido_id(), (producto["id"], 3))
    despues = cliente.get(f"/api/productos/{producto['id']}")

    assert despues.headers["X-Cache"] == "MISS"
    assert despues.json()["stock"] == 7


# ---------- POST /api/reservas ----------

def test_reservar_descuenta_todos_los_items(cliente):
    a = crear_producto(cliente, stock=5)
    b = crear_producto(cliente, stock=5)

    respuesta = reservar(cliente, nuevo_pedido_id(), (a["id"], 2), (b["id"], 5))

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "reservada"
    assert stock_actual(cliente, a["id"]) == 3
    assert stock_actual(cliente, b["id"]) == 0


def test_si_falta_stock_de_un_item_se_devuelve_lo_ya_descontado(cliente):
    a = crear_producto(cliente, stock=5)
    b = crear_producto(cliente, stock=1)

    respuesta = reservar(cliente, nuevo_pedido_id(), (a["id"], 2), (b["id"], 2))

    assert respuesta.status_code == 409
    assert respuesta.json()["estado"] == "rechazada"
    assert b["id"] in respuesta.json()["motivo"]
    assert stock_actual(cliente, a["id"]) == 5
    assert stock_actual(cliente, b["id"]) == 1


def test_producto_inexistente_en_la_reserva_la_rechaza(cliente):
    a = crear_producto(cliente, stock=5)

    respuesta = reservar(cliente, nuevo_pedido_id(), (a["id"], 1), ("prod_noexiste", 1))

    assert respuesta.status_code == 409
    assert "No existe" in respuesta.json()["motivo"]
    assert stock_actual(cliente, a["id"]) == 5


def test_reservar_dos_veces_el_mismo_pedido_descuenta_una_sola_vez(cliente):
    producto = crear_producto(cliente, stock=10)
    pedido_id = nuevo_pedido_id()

    primera = reservar(cliente, pedido_id, (producto["id"], 3))
    segunda = reservar(cliente, pedido_id, (producto["id"], 3))

    assert primera.status_code == segunda.status_code == 200
    assert primera.json() == segunda.json()
    assert stock_actual(cliente, producto["id"]) == 7


def test_reintentar_un_pedido_rechazado_sigue_rechazado(cliente):
    producto = crear_producto(cliente, stock=1)
    pedido_id = nuevo_pedido_id()
    reservar(cliente, pedido_id, (producto["id"], 2))

    respuesta = reservar(cliente, pedido_id, (producto["id"], 1))

    assert respuesta.status_code == 409
    assert stock_actual(cliente, producto["id"]) == 1


def test_producto_repetido_en_el_pedido_se_suma(cliente):
    producto = crear_producto(cliente, stock=3)

    respuesta = reservar(cliente, nuevo_pedido_id(), (producto["id"], 2), (producto["id"], 2))

    assert respuesta.status_code == 409
    assert stock_actual(cliente, producto["id"]) == 3


@pytest.mark.parametrize(
    "body",
    [
        {"pedido_id": "123", "items": [{"producto_id": "prod_1", "cantidad": 1}]},
        {"pedido_id": "ord_1", "items": []},
        {"pedido_id": "ord_1", "items": [{"producto_id": "prod_1", "cantidad": 0}]},
    ],
)
def test_reserva_invalida_da_422(cliente, body):
    assert cliente.post("/api/reservas", json=body).status_code == 422


# ---------- POST /api/reservas/{pedido_id}/liberar ----------

def test_liberar_devuelve_el_stock(cliente):
    a = crear_producto(cliente, stock=5)
    b = crear_producto(cliente, stock=5)
    pedido_id = nuevo_pedido_id()
    reservar(cliente, pedido_id, (a["id"], 2), (b["id"], 1))

    respuesta = cliente.post(f"/api/reservas/{pedido_id}/liberar")

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "liberada"
    assert stock_actual(cliente, a["id"]) == 5
    assert stock_actual(cliente, b["id"]) == 5


def test_liberar_dos_veces_devuelve_una_sola_vez(cliente):
    producto = crear_producto(cliente, stock=5)
    pedido_id = nuevo_pedido_id()
    reservar(cliente, pedido_id, (producto["id"], 2))

    cliente.post(f"/api/reservas/{pedido_id}/liberar")
    cliente.post(f"/api/reservas/{pedido_id}/liberar")

    assert stock_actual(cliente, producto["id"]) == 5


def test_liberar_un_pedido_rechazado_no_devuelve_nada(cliente):
    producto = crear_producto(cliente, stock=1)
    pedido_id = nuevo_pedido_id()
    reservar(cliente, pedido_id, (producto["id"], 2))

    respuesta = cliente.post(f"/api/reservas/{pedido_id}/liberar")

    assert respuesta.json()["estado"] == "rechazada"
    assert stock_actual(cliente, producto["id"]) == 1


def test_si_liberar_llega_antes_que_reservar_no_se_descuenta(cliente):
    producto = crear_producto(cliente, stock=5)
    pedido_id = nuevo_pedido_id()

    liberar = cliente.post(f"/api/reservas/{pedido_id}/liberar")
    respuesta = reservar(cliente, pedido_id, (producto["id"], 2))

    assert liberar.status_code == 200
    assert respuesta.status_code == 409
    assert respuesta.json()["estado"] == "liberada"
    assert stock_actual(cliente, producto["id"]) == 5


def test_reservar_despues_de_liberar_no_vuelve_a_descontar(cliente):
    producto = crear_producto(cliente, stock=5)
    pedido_id = nuevo_pedido_id()
    reservar(cliente, pedido_id, (producto["id"], 2))
    cliente.post(f"/api/reservas/{pedido_id}/liberar")

    respuesta = reservar(cliente, pedido_id, (producto["id"], 2))

    assert respuesta.status_code == 409
    assert stock_actual(cliente, producto["id"]) == 5
