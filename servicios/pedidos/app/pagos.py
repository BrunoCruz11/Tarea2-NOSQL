"""Pasarela de pagos SIMULADA (no cobra nada de verdad).

Para poder mostrar en la demo una saga que falla en el pago, rechaza:
  - cualquier pago con el método TARJETA_RECHAZADA, y
  - cualquier pago por encima de LIMITE_PAGO (5000 por defecto, en el docker-compose).
"""

from uuid import uuid4

from app import config
from app.modelos import MetodoPago


class PagoRechazado(Exception):
    pass


async def cobrar(monto: float, metodo: MetodoPago) -> str:
    """Cobra y devuelve el id de la transacción. Levanta PagoRechazado si no se aprueba."""
    if metodo == MetodoPago.TARJETA_RECHAZADA:
        raise PagoRechazado("La tarjeta fue rechazada por el emisor")
    if monto > config.LIMITE_PAGO:
        raise PagoRechazado(f"El monto {monto:.2f} supera el límite de {config.LIMITE_PAGO:.2f}")
    return f"tx_{uuid4().hex[:12]}"


async def reembolsar(transaccion_id: str) -> None:
    """Devuelve el dinero de una transacción aprobada. En la simulación siempre funciona."""
