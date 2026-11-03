"""Chain of Responsibility: el orden de los manejadores y dónde se corta cada pedido."""

import asyncio

import pytest

from app.cadena import ContextoPedido, ErrorDePedido, construir_cadena
from app.cadena.base import Manejador


def body(*items: tuple[str, int], usuario_id="usr_1", metodo_pago="TARJETA_CREDITO") -> dict:
    return {
        "usuario_id": usuario_id,
        "items": [{"producto_id": p, "cantidad": c} for p, c in items],
        "metodo_pago": metodo_pago,
    }


def correr_cadena(cuerpo, clave="clave-1") -> ContextoPedido:
    contexto = ContextoPedido(body=cuerpo, idempotency_key=clave)
    asyncio.run(construir_cadena().manejar(contexto))
    return contexto


def error_de_la_cadena(cuerpo, clave="clave-1") -> ErrorDePedido:
    with pytest.raises(ErrorDePedido) as error:
        correr_cadena(cuerpo, clave)
    return error.value


# ---------- La clase base ----------

class Anotador(Manejador):
    """Manejador de prueba: anota su nombre y, si se le pide, corta la cadena."""

    def __init__(self, nombre, ejecutados, falla=False, termina=False):
        super().__init__()
        self.nombre, self.ejecutados, self.falla, self.termina = nombre, ejecutados, falla, termina

    async def procesar(self, contexto):
        self.ejecutados.append(self.nombre)
        if self.falla:
            raise ErrorDePedido(400, f"falló {self.nombre}")
        if self.termina:
            contexto.terminado = True


def test_los_manejadores_se_ejecutan_en_el_orden_en_que_se_enlazan():
    ejecutados = []
    inicio = Anotador("a", ejecutados)
    inicio.enlazar(Anotador("b", ejecutados)).enlazar(Anotador("c", ejecutados))

    asyncio.run(inicio.manejar(ContextoPedido(body=None)))

    assert ejecutados == ["a", "b", "c"]


def test_un_error_corta_la_cadena_y_los_siguientes_no_se_ejecutan():
    ejecutados = []
    inicio = Anotador("a", ejecutados)
    inicio.enlazar(Anotador("b", ejecutados, falla=True)).enlazar(Anotador("c", ejecutados))

    with pytest.raises(ErrorDePedido, match="falló b"):
        asyncio.run(inicio.manejar(ContextoPedido(body=None)))
    assert ejecutados == ["a", "b"]


def test_un_manejador_puede_terminar_la_cadena_sin_error():
    ejecutados = []
    inicio = Anotador("a", ejecutados, termina=True)
    inicio.enlazar(Anotador("b", ejecutados))

    asyncio.run(inicio.manejar(ContextoPedido(body=None)))

    assert ejecutados == ["a"]


def test_la_cadena_de_pedidos_tiene_los_manejadores_en_orden():
    nombres, manejador = [], construir_cadena()
    while manejador is not None:
        nombres.append(type(manejador).__name__)
        manejador = manejador._siguiente

    assert nombres == [
        "ValidadorDeDatos", "ValidadorDeIdempotencia", "ValidadorDeUsuario",
        "ValidadorDeStock", "CalculadorDeTotal", "IniciadorDeSaga",
    ]


# ---------- Dónde se corta cada pedido ----------

def test_body_invalido_lo_corta_validador_de_datos_sin_llamar_a_ningun_servicio(mongo, servicios):
    error = error_de_la_cadena({"usuario_id": "usr_1", "items": []})

    assert error.status_code == 422
    assert servicios.llamadas == []
    assert mongo.pedidos == {}


def test_sin_idempotency_key_lo_corta_validador_de_idempotencia(mongo, servicios):
    error = error_de_la_cadena(body(("prod_1", 1)), clave=None)

    assert error.status_code == 400
    assert servicios.llamadas == []


def test_usuario_inexistente_lo_corta_validador_de_usuario_antes_del_stock(mongo, servicios):
    error = error_de_la_cadena(body(("prod_1", 1), usuario_id="usr_noexiste"))

    assert error.status_code == 404
    assert servicios.llamadas == ["obtener_usuario"]


def test_usuarios_caido_da_503(mongo, servicios):
    servicios.caidos["obtener_usuario"] = float("inf")

    assert error_de_la_cadena(body(("prod_1", 1))).status_code == 503


def test_producto_inexistente_lo_corta_validador_de_stock(mongo, servicios):
    error = error_de_la_cadena(body(("prod_noexiste", 1)))

    assert error.status_code == 404
    assert "prod_noexiste" in error.detalle


def test_sin_stock_lo_corta_validador_de_stock_y_la_saga_no_arranca(mongo, servicios):
    error = error_de_la_cadena(body(("prod_1", 6)))  # hay 5

    assert error.status_code == 409
    assert "reservar_stock" not in servicios.llamadas
    assert mongo.pedidos == {}  # no se creó ningún pedido


def test_el_total_se_calcula_con_los_precios_del_catalogo_sin_errores_de_redondeo(mongo, servicios):
    # Con float: 0.1 * 3 + 0.2 * 3 = 0.9000000000000001
    contexto = correr_cadena(body(("prod_centavos_1", 3), ("prod_centavos_2", 3)))

    assert contexto.total == 0.9
    assert [linea.precio_unitario for linea in contexto.lineas] == [0.1, 0.2]


def test_si_pasa_todas_las_validaciones_arranca_la_saga_y_se_confirma(mongo, servicios):
    contexto = correr_cadena(body(("prod_1", 2)))

    assert contexto.status_code == 201
    assert contexto.pedido.estado == "CONFIRMADO"
    assert contexto.pedido.total == 20


def test_el_mismo_pedido_otra_vez_devuelve_el_existente_sin_llamar_a_los_servicios(mongo, servicios):
    primero = correr_cadena(body(("prod_1", 2)))
    llamadas_del_primero = list(servicios.llamadas)

    segundo = correr_cadena(body(("prod_1", 2)))

    assert segundo.status_code == 200
    assert segundo.pedido.pedido_id == primero.pedido.pedido_id
    assert servicios.llamadas == llamadas_del_primero  # el segundo no consultó nada
    assert len(mongo.pedidos) == 1


def test_la_misma_clave_con_otro_pedido_da_422(mongo, servicios):
    correr_cadena(body(("prod_1", 2)))

    assert error_de_la_cadena(body(("prod_1", 3))).status_code == 422
