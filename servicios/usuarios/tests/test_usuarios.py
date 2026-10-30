"""Tests de punta a punta contra el servicio levantado con docker compose.

    pip install -r servicios/usuarios/tests/requirements.txt
    pytest servicios/usuarios/tests

Cada test crea sus propios usuarios con email y documento al azar, así se pueden
correr muchas veces sin borrar la base.
"""

import csv
import hashlib
import hmac
import io
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


# ---------- GET /api/usuarios/exportar ----------

# La misma que en el docker-compose: con ella el test calcula el hash esperado.
CLAVE_SEUDONIMIZACION = os.environ.get("CLAVE_SEUDONIMIZACION", "clave-de-demo-shopexpress")


def seudonimo(valor: str) -> str:
    return hmac.new(CLAVE_SEUDONIMIZACION.encode(), valor.encode(), hashlib.sha256).hexdigest()


def buscar_exportado(exportados: list[dict], usuario: dict) -> dict:
    return next(u for u in exportados if u["email_hash"] == seudonimo(usuario["email"]))


def test_exportar_json_seudonimiza_los_datos_personales(cliente):
    usuario = crear_usuario(cliente, nombre="Juan Carlos", apellido="Pérez Gómez")

    respuesta = cliente.get("/api/usuarios/exportar")

    assert respuesta.status_code == 200
    exportado = buscar_exportado(respuesta.json(), usuario)
    assert exportado == {
        "tipo_documento": "DNI",
        "documento_hash": seudonimo(f"DNI:{usuario['nro_documento']}"),
        "email_hash": seudonimo(usuario["email"]),
        "nombre": "J. C.",
        "apellido": "P. G.",
        "direcciones": [{"ciudad": "Montevideo", "codigo_postal": "11000"}],
        "cantidad_pedidos": 0,
    }
    # Nada que identifique a la persona aparece en la respuesta.
    for dato in (usuario["email"], usuario["nro_documento"], "Juan", "Pérez", "Av. Principal"):
        assert dato not in respuesta.text


def test_exportar_cuenta_los_pedidos_del_historial(cliente):
    usuario = crear_usuario(cliente)
    cliente.post(f"/api/usuarios/{usuario['id']}/historial", json={"pedido_id": "ord_exp1"})
    cliente.post(f"/api/usuarios/{usuario['id']}/historial", json={"pedido_id": "ord_exp2"})

    exportado = buscar_exportado(cliente.get("/api/usuarios/exportar").json(), usuario)

    assert exportado["cantidad_pedidos"] == 2


def test_exportar_csv_se_descarga_como_archivo(cliente):
    usuario = crear_usuario(cliente)

    respuesta = cliente.get("/api/usuarios/exportar", params={"formato": "csv"})

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert "attachment" in respuesta.headers["content-disposition"]
    filas = list(csv.DictReader(io.StringIO(respuesta.text)))
    fila = next(f for f in filas if f["email_hash"] == seudonimo(usuario["email"]))
    assert fila["nombre"] == "J."
    assert fila["direcciones"] == "Montevideo (11000)"
    assert fila["cantidad_pedidos"] == "0"
    assert usuario["email"] not in respuesta.text


def test_exportar_con_formato_desconocido_da_422(cliente):
    assert cliente.get("/api/usuarios/exportar", params={"formato": "xml"}).status_code == 422
