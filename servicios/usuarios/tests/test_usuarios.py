"""Tests de punta a punta contra el servicio levantado con docker compose.

    pip install -r servicios/usuarios/tests/requirements.txt
    pytest servicios/usuarios/tests

Cada test crea sus propios usuarios con email y documento al azar, así se pueden
correr muchas veces sin borrar la base.
"""

import os
from uuid import uuid4

import httpx
import pytest

URL = os.environ.get("USUARIOS_URL", "http://localhost:8001")


@pytest.fixture
def cliente():
    with httpx.Client(base_url=URL, timeout=10) as cliente:
        yield cliente


def datos_usuario(**cambios) -> dict:
    sufijo = uuid4().hex[:10]
    datos = {
        "tipo_documento": "DNI",
        "nro_documento": sufijo,
        "nombre": "Juan",
        "apellido": "Pérez",
        "email": f"juan.{sufijo}@example.com",
        "direcciones": [{"calle": "Av. Principal 123", "ciudad": "Montevideo", "codigo_postal": "11000"}],
    }
    return {**datos, **cambios}


def crear_usuario(cliente, **cambios) -> dict:
    respuesta = cliente.post("/api/usuarios", json=datos_usuario(**cambios))
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# ---------- POST /api/usuarios ----------

def test_crear_usuario_devuelve_id_con_prefijo_e_historial_vacio(cliente):
    datos = datos_usuario()
    respuesta = cliente.post("/api/usuarios", json=datos)

    assert respuesta.status_code == 201
    usuario = respuesta.json()
    assert usuario["id"].startswith("usr_")
    assert usuario["historial_pedidos"] == []
    assert usuario["direcciones"] == datos["direcciones"]


def test_email_repetido_da_409_aunque_cambien_mayusculas(cliente):
    usuario = crear_usuario(cliente)

    respuesta = cliente.post("/api/usuarios", json=datos_usuario(email=usuario["email"].upper()))

    assert respuesta.status_code == 409
    assert "email" in respuesta.json()["detail"]


def test_documento_repetido_da_409(cliente):
    usuario = crear_usuario(cliente)

    respuesta = cliente.post(
        "/api/usuarios",
        json=datos_usuario(tipo_documento="dni", nro_documento=usuario["nro_documento"]),
    )

    assert respuesta.status_code == 409
    assert "documento" in respuesta.json()["detail"]


def test_mismo_numero_con_otro_tipo_de_documento_se_permite(cliente):
    usuario = crear_usuario(cliente)

    respuesta = cliente.post(
        "/api/usuarios",
        json=datos_usuario(tipo_documento="PASAPORTE", nro_documento=usuario["nro_documento"]),
    )

    assert respuesta.status_code == 201


@pytest.mark.parametrize(
    "cambios",
    [{"email": "no-es-un-email"}, {"nombre": ""}, {"direcciones": [{"calle": "Sin ciudad"}]}],
)
def test_datos_invalidos_dan_422(cliente, cambios):
    respuesta = cliente.post("/api/usuarios", json=datos_usuario(**cambios))

    assert respuesta.status_code == 422


# ---------- GET /api/usuarios/{id} ----------

def test_obtener_usuario_primero_de_mongo_y_despues_de_redis(cliente):
    usuario = crear_usuario(cliente)

    primera = cliente.get(f"/api/usuarios/{usuario['id']}")
    segunda = cliente.get(f"/api/usuarios/{usuario['id']}")

    assert primera.status_code == 200
    assert primera.headers["X-Cache"] == "MISS"
    assert segunda.headers["X-Cache"] == "HIT"
    assert primera.json() == segunda.json() == usuario


def test_usuario_inexistente_da_404(cliente):
    respuesta = cliente.get("/api/usuarios/usr_noexiste")

    assert respuesta.status_code == 404


# ---------- POST /api/usuarios/{id}/historial (interno) ----------

def test_agregar_pedido_invalida_la_cache(cliente):
    usuario = crear_usuario(cliente)
    cliente.get(f"/api/usuarios/{usuario['id']}")  # queda en caché

    respuesta = cliente.post(f"/api/usuarios/{usuario['id']}/historial", json={"pedido_id": "ord_1"})
    despues = cliente.get(f"/api/usuarios/{usuario['id']}")

    assert respuesta.status_code == 200
    assert respuesta.json()["historial_pedidos"] == ["ord_1"]
    assert despues.headers["X-Cache"] == "MISS"
    assert despues.json()["historial_pedidos"] == ["ord_1"]


def test_agregar_el_mismo_pedido_dos_veces_no_lo_duplica(cliente):
    usuario = crear_usuario(cliente)
    url = f"/api/usuarios/{usuario['id']}/historial"

    cliente.post(url, json={"pedido_id": "ord_1"})
    cliente.post(url, json={"pedido_id": "ord_2"})
    respuesta = cliente.post(url, json={"pedido_id": "ord_1"})

    assert respuesta.json()["historial_pedidos"] == ["ord_1", "ord_2"]


def test_agregar_pedido_a_usuario_inexistente_da_404(cliente):
    respuesta = cliente.post("/api/usuarios/usr_noexiste/historial", json={"pedido_id": "ord_1"})

    assert respuesta.status_code == 404


def test_pedido_id_sin_prefijo_da_422(cliente):
    usuario = crear_usuario(cliente)

    respuesta = cliente.post(f"/api/usuarios/{usuario['id']}/historial", json={"pedido_id": "123"})

    assert respuesta.status_code == 422
