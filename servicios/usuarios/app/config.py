import os

NOMBRE_SERVICIO = "usuarios"

MONGO_URL = os.environ["MONGO_URL"]
MONGO_DB = os.environ.get("MONGO_DB", "usuarios")
REDIS_URL = os.environ["REDIS_URL"]
CACHE_TTL_SEGUNDOS = int(os.environ.get("CACHE_TTL_SEGUNDOS", "60"))
