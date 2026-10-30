"""Seudonimización de los datos personales para GET /api/usuarios/exportar.

  - email y documento: HMAC-SHA256 con una clave secreta (CLAVE_SEUDONIMIZACION).
    Un SHA-256 solo no alcanza: un documento son 8 cifras y se pueden probar todos
    en segundos hasta dar con el hash. Sin la clave eso no se puede hacer.
    El mismo dato da siempre el mismo hash, así se pueden cruzar dos exportaciones
    (contar usuarios distintos, por ejemplo) sin saber quién es quién.
  - nombre y apellido: solo las iniciales ("Juan Carlos" → "J. C.").
  - direcciones: solo ciudad y código postal; la calle identifica a la persona.

Técnicamente es seudonimización y no anonimización total: quien tenga la clave
puede volver a calcular el hash de un email conocido y encontrarlo en el archivo.
"""

import hashlib
import hmac

from app import config
from app.modelos import DireccionAnonima, Usuario, UsuarioAnonimo


def seudonimo(valor: str) -> str:
    return hmac.new(config.CLAVE_SEUDONIMIZACION.encode(), valor.encode(), hashlib.sha256).hexdigest()


def iniciales(texto: str) -> str:
    return " ".join(f"{palabra[0].upper()}." for palabra in texto.split())


def anonimizar(usuario: Usuario) -> UsuarioAnonimo:
    return UsuarioAnonimo(
        tipo_documento=usuario.tipo_documento,
        # El documento es único por (tipo, número): se hashean juntos.
        documento_hash=seudonimo(f"{usuario.tipo_documento}:{usuario.nro_documento}"),
        email_hash=seudonimo(usuario.email),
        nombre=iniciales(usuario.nombre),
        apellido=iniciales(usuario.apellido),
        direcciones=[
            DireccionAnonima(ciudad=d.ciudad, codigo_postal=d.codigo_postal) for d in usuario.direcciones
        ],
        cantidad_pedidos=len(usuario.historial_pedidos),
    )
