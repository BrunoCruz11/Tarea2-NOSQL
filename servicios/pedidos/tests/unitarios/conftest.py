"""Tests unitarios de la cadena y de la saga: corren solos, sin docker.

Mongo y los otros servicios (usuarios, catálogo) se reemplazan por versiones
falsas en memoria. Así se puede forzar cada caso (un servicio caído, un timeout,
una compensación que falla) y ver qué llamadas hizo pedidos, sin levantar nada.

    pip install -r servicios/pedidos/tests/requirements.txt
    pytest servicios/pedidos/tests/unitarios
"""

import os
import sys
from pathlib import Path

import pytest

# config.py lee estas variables al importarse; nadie se conecta de verdad.
# Se ponen solo mientras se importa `app` y después se restauran: si no, los tests
# HTTP (que leen USUARIOS_URL y CATALOGO_URL) apuntarían a "no-se-usa" al correr
# `pytest servicios` entero.
_FALSAS = {
    "MONGO_URL": "mongodb://no-se-usa:27017",
    "USUARIOS_URL": "http://no-se-usa",
    "CATALOGO_URL": "http://no-se-usa",
}
_originales = {nombre: os.environ.get(nombre) for nombre in _FALSAS}
for nombre, valor in _FALSAS.items():
    os.environ.setdefault(nombre, valor)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # servicios/pedidos, para importar `app`

from app import clientes, repositorio, saga  # noqa: E402
from app.modelos import Pedido  # noqa: E402

for nombre, valor in _originales.items():
    if valor is None:
        os.environ.pop(nombre, None)
    else:
        os.environ[nombre] = valor


class MongoFalso:
    """Reemplaza al repositorio de pedidos: guarda los pedidos en un dict."""

    def __init__(self):
        self.pedidos: dict[str, dict] = {}
        self.estados: list[str] = []  # el estado del pedido después de cada paso registrado

    async def crear(self, pedido: Pedido) -> None:
        if any(p["idempotency_key"] == pedido.idempotency_key for p in self.pedidos.values()):
            raise repositorio.PedidoDuplicado(pedido.idempotency_key)
        self.pedidos[pedido.pedido_id] = pedido.model_dump(mode="json")

    async def obtener_por_clave(self, clave: str) -> Pedido | None:
        documento = next((p for p in self.pedidos.values() if p["idempotency_key"] == clave), None)
        return Pedido.model_validate(documento) if documento else None

    async def registrar_paso(self, pedido_id, entrada, cambios) -> Pedido:
        documento = self.pedidos[pedido_id]
        documento["historial"].append(entrada.model_dump(mode="json"))
        for campo, valor in cambios.items():
            *ruta, ultimo = campo.split(".")  # "pago.estado_pago" → documento["pago"]["estado_pago"]
            destino = documento
            for parte in ruta:
                destino = destino[parte]
            destino[ultimo] = valor
        self.estados.append(str(documento["estado"]))
        return Pedido.model_validate(documento)


class ServiciosFalsos:
    """Reemplaza a clientes.py (las llamadas HTTP a usuarios y catálogo) y anota cada llamada.

    - caidos: cuántas veces seguidas falla cada operación con ServicioNoDisponible
      (float("inf") = siempre).
    - estado_reserva: lo que responde catálogo al reservar ("reservada" o "rechazada").
    """

    def __init__(self):
        self.llamadas: list[str] = []
        self.caidos: dict[str, float] = {}
        self.estado_reserva = "reservada"
        self.usuarios = {"usr_1": {"id": "usr_1", "nombre": "Juan"}}
        self.productos = {
            "prod_1": {"id": "prod_1", "precio": 10.0, "stock": 5},
            "prod_centavos_1": {"id": "prod_centavos_1", "precio": 0.1, "stock": 5},
            "prod_centavos_2": {"id": "prod_centavos_2", "precio": 0.2, "stock": 5},
        }

    def _llamar(self, operacion: str) -> None:
        self.llamadas.append(operacion)
        if self.caidos.get(operacion, 0) > 0:
            self.caidos[operacion] -= 1
            raise clientes.ServicioNoDisponible("falso")

    async def obtener_usuario(self, usuario_id):
        self._llamar("obtener_usuario")
        return self.usuarios.get(usuario_id)

    async def obtener_producto(self, producto_id):
        self._llamar("obtener_producto")
        return self.productos.get(producto_id)

    async def reservar_stock(self, pedido_id, items):
        self._llamar("reservar_stock")
        return {"pedido_id": pedido_id, "estado": self.estado_reserva, "motivo": "Stock insuficiente de prod_1"}

    async def liberar_stock(self, pedido_id):
        self._llamar("liberar_stock")

    async def agregar_al_historial(self, usuario_id, pedido_id):
        self._llamar("agregar_al_historial")
        return usuario_id in self.usuarios


@pytest.fixture
def mongo(monkeypatch) -> MongoFalso:
    falso = MongoFalso()
    for nombre in ("crear", "obtener_por_clave", "registrar_paso"):
        monkeypatch.setattr(repositorio, nombre, getattr(falso, nombre))
    return falso


@pytest.fixture
def servicios(monkeypatch) -> ServiciosFalsos:
    falso = ServiciosFalsos()
    for nombre in ("obtener_usuario", "obtener_producto", "reservar_stock", "liberar_stock", "agregar_al_historial"):
        monkeypatch.setattr(clientes, nombre, getattr(falso, nombre))

    # Los reintentos de las compensaciones esperan entre intento e intento; acá no hace falta.
    async def sin_espera(segundos):
        pass

    monkeypatch.setattr(saga.asyncio, "sleep", sin_espera)
    return falso
