import os

NOMBRE_SERVICIO = "pedidos"

MONGO_URL = os.environ["MONGO_URL"]
MONGO_DB = os.environ.get("MONGO_DB", "pedidos")

USUARIOS_URL = os.environ["USUARIOS_URL"]
CATALOGO_URL = os.environ["CATALOGO_URL"]

# Pago simulado: la pasarela rechaza cualquier cobro por encima de este monto.
LIMITE_PAGO = float(os.environ.get("LIMITE_PAGO", "5000"))
