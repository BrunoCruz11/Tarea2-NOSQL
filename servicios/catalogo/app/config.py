import os

NOMBRE_SERVICIO = "catalogo"

MONGO_URL = os.environ["MONGO_URL"]
MONGO_DB = os.environ.get("MONGO_DB", "catalogo")
REDIS_URL = os.environ["REDIS_URL"]
# Más corto que en usuarios: el stock cambia seguido y la caché lo muestra.
CACHE_TTL_SEGUNDOS = int(os.environ.get("CACHE_TTL_SEGUNDOS", "30"))
