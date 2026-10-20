import os

NOMBRE_SERVICIO = "catalogo"

MONGO_URL = os.environ["MONGO_URL"]
MONGO_DB = os.environ.get("MONGO_DB", "catalogo")
REDIS_URL = os.environ["REDIS_URL"]
