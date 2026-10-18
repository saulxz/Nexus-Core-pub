"""Verificación en dos pasos (TOTP, RFC 6238) con la biblioteca estándar.

No se añade ninguna dependencia: el algoritmo se implementa con ``hmac``,
``hashlib``, ``struct`` y ``base64`` de la stdlib.

::

    código = Truncar(HMAC-SHA1(secreto, contador) mod 10^6)
    contador = int(instante Unix) // 30

* **SHA-1** no es una debilidad aquí: es el HMAC fijado por la RFC 6238 (y el
  único que aceptan las apps autenticadoras del mercado). La resistencia del
  segundo factor viene del secreto, que solo conoce el servidor y el teléfono.
* La **ventana ±1** (dos códigos válidos) absorbe el desfase del reloj del móvil
  sin ampliar la ventana de replay más de un periodo.
* La comparación se hace con :func:`hmac.compare_digest` (tiempo constante).

Todos los cálculos aceptan un ``timestamp`` explícito: los tests verifican un
código generado para un instante concreto sin depender del reloj real.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import urllib.parse

#: Digitos del codigo (6, como en Google Authenticator/Aegis).
DIGITS = 6

#: Periodo del paso temporal en segundos (RFC 6238 recomienda 30).
PERIOD = 30

#: Ventana de tolerancia: se aceptan los periodos anterior y siguiente (±30 s).
WINDOW = 1

#: Longitud del secreto en bytes antes de codificar en base32.
SECRET_BYTES = 32

#: Numero de codigos de recuperacion generados en el alta del segundo factor.
RECOVERY_CODES = 10


# ============================== Secreto =================================
def generate_secret() -> str:
    """Genera un secreto TOTP aleatorio en base32 sin relleno (``A-Z2-7``)."""
    return base64.b32encode(os.urandom(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """Convierte el secreto base32 (con o sin ``=``) a bytes."""
    padded = (secret or "").strip().upper()
    padded += "=" * (-len(padded) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("El secreto TOTP no es un base32 válido.") from exc


def provisioning_uri(secret: str, account: str, issuer: str = "Nexus-Core") -> str:
    """Devuelve la URL ``otpauth://totp/...`` para escanear con la app.

    Lleva el secreto en el propio *query string*: es la forma que define el
    estándar y solo la lee la aplicación autenticadora del operador.
    """
    label = urllib.parse.quote(f"{issuer}:{account}", safe="")
    params = urllib.parse.urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD}
    )
    return f"otpauth://totp/{label}?{params}"


# ============================== Algoritmo ===============================
def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    """Código HOTP (RFC 4226) para un contador dado."""
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", int(counter)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp_code(secret: str, timestamp: float | None = None, period: int = PERIOD) -> str:
    """Código TOTP válido en el instante indicado (por defecto, ahora)."""
    moment = time.time() if timestamp is None else float(timestamp)
    return hotp(secret, int(moment // period), DIGITS)


def verify(secret: str, code: str, timestamp: float | None = None, window: int = WINDOW) -> bool:
    """Comprueba un código TOTP con tolerancia de ±``window`` periodos."""
    cleaned = (code or "").strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return False
    moment = time.time() if timestamp is None else float(timestamp)
    counter = int(moment // PERIOD)
    return any(
        hmac.compare_digest(hotp(secret, counter + offset, DIGITS), cleaned)
        for offset in range(-max(0, window), max(0, window) + 1)
    )


# ========================= Códigos de recuperación =======================
def generate_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """Genera códigos de recuperación de un solo uso (formato ``XXXX-XXXX``)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin I/O/0/1 (evita confusiones)
    return [
        "-".join(
            "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)
        )
        for _ in range(max(1, int(count)))
    ]


def hash_recovery_code(code: str) -> str:
    """Normaliza yresume un código de recuperación para almacenarlo hasheado."""
    normalized = (code or "").strip().upper().replace(" ", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "DIGITS",
    "PERIOD",
    "RECOVERY_CODES",
    "SECRET_BYTES",
    "WINDOW",
    "generate_recovery_codes",
    "generate_secret",
    "hash_recovery_code",
    "hotp",
    "provisioning_uri",
    "totp_code",
    "verify",
]