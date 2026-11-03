"""SAGA: los pasos, las compensaciones en orden inverso y qué pasa cuando falla cada uno."""

import asyncio

import pytest

from app import pagos, repositorio, saga
from app.cadena import ContextoPedido
from app.modelos import ItemPedido, LineaPedido, MetodoPago, PedidoCrear

PASOS_OK = [("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "OK"), ("PROCESAR_PAGO", "OK"), ("CONFIRMAR_PEDIDO", "OK")]


def contexto(metodo_pago=MetodoPago.TARJETA_CREDITO, precio=10.0, cantidad=2) -> ContextoPedido:
    """Un pedido que ya pasó por la cadena: datos validados, líneas y total calculados."""
    return ContextoPedido(
        body=None,
        idempotency_key="clave-1",
        datos=PedidoCrear(
            usuario_id="usr_1", items=[ItemPedido(producto_id="prod_1", cantidad=cantidad)], metodo_pago=metodo_pago
        ),
        lineas=[LineaPedido(producto_id="prod_1", cantidad=cantidad, precio_unitario=precio)],
        total=precio * cantidad,
    )


def correr_saga(ctx=None):
    return asyncio.run(saga.ejecutar(ctx or contexto()))


def pasos(pedido) -> list[tuple[str, str]]:
    return [(paso.paso, paso.resultado) for paso in pedido.historial]


# ---------- Camino feliz ----------

def test_camino_feliz_ejecuta_los_cuatro_pasos_y_confirma(mongo, servicios):
    pedido, codigo = correr_saga()

    assert codigo == 201
    assert pedido.estado == "CONFIRMADO"
    assert pedido.pago.estado_pago == "APROBADO"
    assert pedido.pago.transaccion_id.startswith("tx_")
    assert pasos(pedido) == PASOS_OK
    assert servicios.llamadas == ["reservar_stock", "agregar_al_historial"]


def test_el_estado_pasa_por_pendiente_procesando_y_confirmado(mongo, servicios):
    correr_saga()

    # Estado después de cada paso: crear, reservar, pagar, confirmar.
    assert mongo.estados == ["PENDIENTE", "PROCESANDO", "PROCESANDO", "CONFIRMADO"]


# ---------- Fallos y compensaciones ----------

def test_sin_stock_en_la_reserva_solo_cancela_el_pedido(mongo, servicios):
    # Pasó ValidadorDeStock, pero otro pedido se llevó el stock antes de la reserva.
    servicios.estado_reserva = "rechazada"

    pedido, codigo = correr_saga()

    assert codigo == 409
    assert pedido.estado == "CANCELADO"
    assert pasos(pedido) == [("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "FALLO"), ("CANCELAR_PEDIDO", "OK")]
    # Catálogo ya devolvió lo que había descontado: no hay que liberar nada.
    assert "liberar_stock" not in servicios.llamadas


def test_pago_rechazado_libera_el_stock_y_cancela_en_orden_inverso(mongo, servicios):
    pedido, codigo = correr_saga(contexto(metodo_pago=MetodoPago.TARJETA_RECHAZADA))

    assert codigo == 402
    assert pedido.estado == "CANCELADO"
    assert pedido.pago.estado_pago == "RECHAZADO"
    assert pasos(pedido) == [
        ("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "OK"), ("PROCESAR_PAGO", "FALLO"),
        ("LIBERAR_STOCK", "OK"), ("CANCELAR_PEDIDO", "OK"),
    ]
    assert servicios.llamadas == ["reservar_stock", "liberar_stock"]


def test_monto_sobre_el_limite_tambien_rechaza_el_pago(mongo, servicios):
    pedido, codigo = correr_saga(contexto(precio=3000, cantidad=2))  # 6000 > LIMITE_PAGO (5000)

    assert codigo == 402
    assert "límite" in pedido.historial[2].detalle


def test_si_falla_el_ultimo_paso_se_compensan_los_tres_anteriores(mongo, servicios):
    servicios.caidos["agregar_al_historial"] = float("inf")  # usuarios se cae justo al confirmar

    pedido, codigo = correr_saga()

    assert codigo == 503
    assert pedido.estado == "CANCELADO"
    assert pedido.pago.estado_pago == "REEMBOLSADO"
    assert pasos(pedido) == [
        ("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "OK"), ("PROCESAR_PAGO", "OK"), ("CONFIRMAR_PEDIDO", "FALLO"),
        ("REEMBOLSAR_PAGO", "OK"), ("LIBERAR_STOCK", "OK"), ("CANCELAR_PEDIDO", "OK"),
    ]


def test_si_catalogo_no_responde_al_reservar_se_libera_por_las_dudas(mongo, servicios):
    # Timeout: no sabemos si catálogo llegó a reservar (paso "incierto"), así que se libera igual.
    servicios.caidos["reservar_stock"] = 1

    pedido, codigo = correr_saga()

    assert codigo == 503
    assert pasos(pedido) == [
        ("CREAR_PEDIDO", "OK"), ("RESERVAR_STOCK", "FALLO"), ("LIBERAR_STOCK", "OK"), ("CANCELAR_PEDIDO", "OK"),
    ]


def test_un_error_inesperado_da_500_y_compensa_tambien_ese_paso(mongo, servicios, monkeypatch):
    async def pasarela_rota(monto, metodo):
        raise RuntimeError("bug en la pasarela")

    monkeypatch.setattr(pagos, "cobrar", pasarela_rota)

    pedido, codigo = correr_saga()

    assert codigo == 500
    assert [paso for paso, _ in pasos(pedido)][-3:] == ["REEMBOLSAR_PAGO", "LIBERAR_STOCK", "CANCELAR_PEDIDO"]


# ---------- Las compensaciones ----------

def test_liberar_el_stock_se_reintenta_si_catalogo_no_responde(mongo, servicios):
    servicios.caidos["liberar_stock"] = 2  # falla dos veces y a la tercera responde

    pedido, _ = correr_saga(contexto(metodo_pago=MetodoPago.TARJETA_RECHAZADA))

    assert servicios.llamadas.count("liberar_stock") == 3
    assert ("LIBERAR_STOCK", "OK") in pasos(pedido)


def test_si_una_compensacion_falla_igual_se_ejecutan_las_demas(mongo, servicios):
    servicios.caidos["liberar_stock"] = float("inf")

    pedido, _ = correr_saga(contexto(metodo_pago=MetodoPago.TARJETA_RECHAZADA))

    assert servicios.llamadas.count("liberar_stock") == 3  # se rindió después de 3 intentos
    assert pasos(pedido)[-2:] == [("LIBERAR_STOCK", "FALLO"), ("CANCELAR_PEDIDO", "OK")]
    assert pedido.estado == "CANCELADO"


# ---------- Idempotencia ----------

def test_si_ya_hay_un_pedido_con_la_misma_clave_la_saga_no_hace_nada(mongo, servicios):
    correr_saga()
    servicios.llamadas.clear()

    with pytest.raises(repositorio.PedidoDuplicado):
        correr_saga()  # misma Idempotency-Key ("clave-1")

    assert servicios.llamadas == []  # ni reservó ni cobró
    assert len(mongo.pedidos) == 1
