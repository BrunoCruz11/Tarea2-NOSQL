"""Tests de punta a punta de POST /api/pedidos: la cadena de validaciones y la saga.

Necesitan los tres servicios levantados con docker compose:

    pip install -r servicios/pedidos/tests/requirements.txt
    pytest servicios/pedidos/tests

Cada test crea su propio usuario y sus productos, así se pueden correr muchas veces.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

PEDIDOS_URL = os.environ.get("PEDIDOS_URL", "http://localhost:8003")
USUARIOS_URL = os.environ.get("USUARIOS_URL", "http://localhost:8001")
CATALOGO_URL = os.environ.get("CATALOGO_URL", "http://localhost:8002")


@pytest.fixture
def cliente():
    with httpx.Client(timeout=10) as cliente:
        yield cliente


@pytest.fixture
def usuario(cliente) -> dict:
    sufijo = uuid4().hex[:10]
    respuesta = cliente.post(f"{USUARIOS_URL}/api/usuarios", json={
        "tipo_documento": "DNI",
        "nro_documento": sufijo,
        "nombre": "Juan",
        "apellido": "Pérez",
        "email": f"juan.{sufijo}@example.com",
    })
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def crear_producto(cliente, precio: float, stock: int = 10) -> dict:
    respuesta = cliente.post(
        f"{CATALOGO_URL}/api/productos", json={"nombre": "Producto de prueba", "precio": precio, "stock": stock}
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def stock_actual(cliente, producto_id: str) -> int:
    return cliente.get(f"{CATALOGO_URL}/api/productos/{producto_id}").json()["stock"]


def crear_pedido(cliente, body):
    return cliente.post(f"{PEDIDOS_URL}/api/pedidos", json=body)


def pedir(cliente, usuario_id: str, *items: tuple[str, int], metodo_pago: str = "TARJETA_CREDITO"):
    return crear_pedido(cliente, {
        "usuario_id": usuario_id,
        "items": [{"producto_id": p, "cantidad": c} for p, c in items],
        "metodo_pago": metodo_pago,
    })


def pasos(pedido: dict) -> list[tuple[str, str]]:
    return [(paso["paso"], paso["resultado"]) for paso in pedido["historial"]]


# ---------- Saga: camino feliz ----------

def test_pedido_exitoso_queda_confirmado_con_la_estructura_del_anexo(cliente, usuario):
    producto = crear_producto(cliente, precio=25.50, stock=10)

    respuesta = pedir(cliente, usuario["id"], (producto["id"], 2))

    assert respuesta.status_code == 201, respuesta.text
    pedido = respuesta.json()
    assert pedido["pedido_id"].startswith("ord_")
    assert pedido["usuario_id"] == usuario["id"]
    assert pedido["estado"] == "CONFIRMADO"
    assert pedido["items"] == [{"producto_id": producto["id"], "cantidad": 2, "precio_unitario": 25.50}]
    assert pedido["total"] == 51.00
    assert pedido["pago"]["metodo"] == "TARJETA_CREDITO"
    assert pedido["pago"]["estado_pago"] == "APROBADO"
    assert pedido["pago"]["transaccion_id"].startswith("tx_")
    assert pasos(pedido) == [
        ("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "OK"), ("PROCESAR_PAGO", "OK"), ("CONFIRMAR_PEDIDO", "OK"),
    ]


def test_pedido_exitoso_descuenta_stock_y_va_al_historial_del_usuario(cliente, usuario):
    producto = crear_producto(cliente, precio=10, stock=10)

    pedido = pedir(cliente, usuario["id"], (producto["id"], 3)).json()

    assert stock_actual(cliente, producto["id"]) == 7
    historial = cliente.get(f"{USUARIOS_URL}/api/usuarios/{usuario['id']}").json()["historial_pedidos"]
    assert pedido["pedido_id"] in historial


def test_el_pedido_queda_guardado_y_se_puede_consultar(cliente, usuario):
    producto = crear_producto(cliente, precio=10)
    pedido = pedir(cliente, usuario["id"], (producto["id"], 1)).json()

    respuesta = cliente.get(f"{PEDIDOS_URL}/api/pedidos/{pedido['pedido_id']}")

    assert respuesta.status_code == 200
    assert respuesta.json() == pedido


def test_pedido_inexistente_da_404(cliente):
    assert cliente.get(f"{PEDIDOS_URL}/api/pedidos/ord_noexiste").status_code == 404


def test_total_con_precios_del_catalogo_sin_errores_de_redondeo(cliente, usuario):
    a = crear_producto(cliente, precio=10.10)
    b = crear_producto(cliente, precio=0.10)

    pedido = pedir(cliente, usuario["id"], (a["id"], 3), (b["id"], 2)).json()

    assert pedido["total"] == 30.50


def test_se_ignoran_los_precios_que_manda_el_cliente(cliente, usuario):
    producto = crear_producto(cliente, precio=100)

    respuesta = crear_pedido(cliente, {
        "usuario_id": usuario["id"],
        "items": [{"producto_id": producto["id"], "cantidad": 1, "precio_unitario": 1}],
        "metodo_pago": "TARJETA_DEBITO",
        "total": 1,
    })

    assert respuesta.json()["total"] == 100


def test_producto_repetido_se_junta_en_una_linea(cliente, usuario):
    producto = crear_producto(cliente, precio=5)

    pedido = pedir(cliente, usuario["id"], (producto["id"], 1), (producto["id"], 2)).json()

    assert [linea["cantidad"] for linea in pedido["items"]] == [3]
    assert pedido["total"] == 15


# ---------- Saga: pago rechazado → compensación ----------

def test_tarjeta_rechazada_cancela_el_pedido_y_devuelve_el_stock(cliente, usuario):
    a = crear_producto(cliente, precio=10, stock=5)
    b = crear_producto(cliente, precio=20, stock=3)

    respuesta = pedir(cliente, usuario["id"], (a["id"], 2), (b["id"], 3), metodo_pago="TARJETA_RECHAZADA")

    assert respuesta.status_code == 402
    pedido = respuesta.json()
    assert pedido["estado"] == "CANCELADO"
    assert pedido["pago"]["estado_pago"] == "RECHAZADO"
    assert pasos(pedido) == [
        ("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "OK"), ("PROCESAR_PAGO", "FALLO"),
        ("LIBERAR_STOCK", "OK"), ("CANCELAR_PEDIDO", "OK"),
    ]
    assert stock_actual(cliente, a["id"]) == 5
    assert stock_actual(cliente, b["id"]) == 3


def test_monto_sobre_el_limite_rechaza_el_pago(cliente, usuario):
    producto = crear_producto(cliente, precio=3000, stock=5)

    respuesta = pedir(cliente, usuario["id"], (producto["id"], 2))

    assert respuesta.status_code == 402
    assert "límite" in respuesta.json()["historial"][2]["detalle"]
    assert stock_actual(cliente, producto["id"]) == 5


def test_pedido_cancelado_no_va_al_historial_del_usuario(cliente, usuario):
    producto = crear_producto(cliente, precio=10)

    pedir(cliente, usuario["id"], (producto["id"], 1), metodo_pago="TARJETA_RECHAZADA")

    assert cliente.get(f"{USUARIOS_URL}/api/usuarios/{usuario['id']}").json()["historial_pedidos"] == []


# ---------- Saga: dos pedidos por la última unidad ----------

def test_dos_pedidos_a_la_vez_por_la_ultima_unidad_solo_uno_se_confirma(cliente, usuario):
    # Los dos pasan ValidadorDeStock (ven stock 1), pero solo uno gana la reserva atómica.
    producto = crear_producto(cliente, precio=10, stock=1)

    with ThreadPoolExecutor(max_workers=2) as hilos:
        respuestas = list(hilos.map(lambda _: pedir(cliente, usuario["id"], (producto["id"], 1)), range(2)))

    # El que pierde recibe 409: de ValidadorDeStock o, si llegó a la saga, de RESERVAR_STOCK.
    assert sorted(r.status_code for r in respuestas) == [201, 409]
    assert stock_actual(cliente, producto["id"]) == 0


# ---------- ValidadorDeDatos ----------

@pytest.mark.parametrize(
    "body",
    [
        {},
        {"usuario_id": "usr_1", "items": [], "metodo_pago": "TARJETA_CREDITO"},
        {"usuario_id": "sin_prefijo", "items": [{"producto_id": "prod_1", "cantidad": 1}], "metodo_pago": "TARJETA_CREDITO"},
        {"usuario_id": "usr_1", "items": [{"producto_id": "prod_1", "cantidad": 0}], "metodo_pago": "TARJETA_CREDITO"},
        {"usuario_id": "usr_1", "items": [{"producto_id": "../usuarios", "cantidad": 1}], "metodo_pago": "TARJETA_CREDITO"},
        {"usuario_id": "usr_1", "items": [{"producto_id": "prod_1", "cantidad": 1}]},
        {"usuario_id": "usr_1", "items": [{"producto_id": "prod_1", "cantidad": 1}], "metodo_pago": "BITCOIN"},
    ],
)
def test_datos_invalidos_dan_422(cliente, body):
    assert crear_pedido(cliente, body).status_code == 422


def test_json_mal_formado_da_422(cliente):
    respuesta = cliente.post(
        f"{PEDIDOS_URL}/api/pedidos", content="{no es json", headers={"Content-Type": "application/json"}
    )

    assert respuesta.status_code == 422


# ---------- ValidadorDeUsuario ----------

def test_usuario_inexistente_da_404(cliente):
    producto = crear_producto(cliente, precio=10)

    respuesta = pedir(cliente, "usr_noexiste", (producto["id"], 1))

    assert respuesta.status_code == 404
    assert "usuario" in respuesta.json()["detail"]


# ---------- ValidadorDeStock ----------

def test_producto_inexistente_da_404(cliente, usuario):
    respuesta = pedir(cliente, usuario["id"], ("prod_noexiste", 1))

    assert respuesta.status_code == 404
    assert "prod_noexiste" in respuesta.json()["detail"]


def test_sin_stock_da_409_y_no_toca_el_inventario(cliente, usuario):
    producto = crear_producto(cliente, precio=10, stock=2)

    respuesta = pedir(cliente, usuario["id"], (producto["id"], 3))

    assert respuesta.status_code == 409
    assert producto["id"] in respuesta.json()["detail"]
    assert stock_actual(cliente, producto["id"]) == 2
