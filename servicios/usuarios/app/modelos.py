"""Modelos (Pydantic): definen qué forma tienen los datos que entran y salen de la API.

FastAPI los usa para validar el body de cada request (si algo no cumple, responde 422
solo, sin llegar a nuestro código) y para armar la respuesta.
"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Direccion(BaseModel):
    """Dirección de envío. Va embebida dentro del usuario, no tiene colección propia."""

    model_config = ConfigDict(str_strip_whitespace=True)

    calle: str = Field(min_length=1, examples=["Av. Principal 123"])
    ciudad: str = Field(min_length=1, examples=["Montevideo"])
    codigo_postal: str = Field(min_length=1, examples=["11000"])


class UsuarioCrear(BaseModel):
    """Body de POST /api/usuarios. No trae id ni historial: esos los pone el servicio."""

    model_config = ConfigDict(str_strip_whitespace=True)

    tipo_documento: str = Field(min_length=1, examples=["DNI"])
    nro_documento: str = Field(min_length=1, examples=["12345678"])
    nombre: str = Field(min_length=1, examples=["Juan"])
    apellido: str = Field(min_length=1, examples=["Pérez"])
    email: EmailStr = Field(examples=["juan.perez@example.com"])
    direcciones: list[Direccion] = []

    # Normalizamos antes de guardar para que el índice único no se saltee con
    # variantes del mismo dato ("dni" vs "DNI", "Juan@Mail.com" vs "juan@mail.com").
    @field_validator("tipo_documento")
    @classmethod
    def tipo_documento_en_mayusculas(cls, valor: str) -> str:
        return valor.upper()

    @field_validator("email")
    @classmethod
    def email_en_minusculas(cls, valor: str) -> str:
        return valor.lower()


class Usuario(UsuarioCrear):
    """Usuario completo, tal como lo devuelve la API (y como se guarda en la caché)."""

    id: str = Field(examples=["usr_3f9a1c2b7d4e"])
    historial_pedidos: list[str] = Field(default=[], examples=[["ord_98765"]])


class PedidoEnHistorial(BaseModel):
    """Body del endpoint interno que usa el servicio de pedidos al confirmar una compra."""

    pedido_id: str = Field(pattern=r"^ord_\w+$", examples=["ord_98765"])
