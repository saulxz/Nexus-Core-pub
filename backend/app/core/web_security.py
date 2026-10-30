"""Utilidades de seguridad web compartidas por middleware y autenticación.

Cloudflare Tunnel entrega la petición al hub por HTTP, pero informa del esquema
original en ``CF-Visitor`` y/o ``X-Forwarded-Proto``. No se confía en valores
arbitrarios de un cliente: solo se usan para decidir el esquema cuando la
petición llega por el proxy local (el hub escucha en 127.0.0.1).
"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from starlette.requests import Request

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
_LOOPBACK = {"127.0.0.1", "::1"}


def host_without_port(request: Request) -> str:
    """Host de la petición sin puerto, en minúsculas."""
    raw = request.headers.get("host", "")
    if raw.startswith("["):
        return raw.split("]", 1)[0].strip("[]").lower()
    return raw.rsplit(":", 1)[0].strip().lower()


def is_local_request(request: Request) -> bool:
    """True para el panel local de pywebview/desarrollo."""
    return host_without_port(request) in _LOCAL_HOSTS


def request_scheme(request: Request) -> str:
    """Esquema original, con prioridad a las cabeceras de Cloudflare."""
    visitor = request.headers.get("cf-visitor", "").strip()
    if visitor:
        try:
            parsed = json.loads(visitor)
            scheme = str(parsed.get("scheme") or "").lower()
            if scheme in {"http", "https"}:
                return scheme
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded in {"http", "https"}:
        return forwarded
    return request.url.scheme


def is_https(request: Request) -> bool:
    """True si la petición original llegó por HTTPS."""
    return request_scheme(request) == "https"


def client_ip(request: Request) -> str:
    """IP real para rate limiting y auditoría.

    Cloudflare entrega la petición desde loopback. Solo en ese caso se confía
    en ``CF-Connecting-IP``/``X-Forwarded-For``; un cliente directo no puede
    falsificar su IP para saltarse el límite.
    """
    peer = request.client.host if request.client else "desconocida"
    if peer not in _LOOPBACK:
        return peer
    candidates = [
        request.headers.get("cf-connecting-ip", "").strip(),
        (request.headers.get("x-forwarded-for", "").split(",")[0].strip()),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return peer


def https_url(request: Request) -> str:
    """URL absoluta de la misma petición forzando HTTPS."""
    parsed = urlsplit(str(request.url))
    host = request.headers.get("host") or parsed.netloc
    return f"https://{host}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")


#: CSP compatible con el panel actual: assets locales, estilos e inline de
#: Tailwind, y WebSocket de la terminal. No permite scripts de terceros.
CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "connect-src 'self' ws: wss:"
)


__all__ = [
    "CSP",
    "client_ip",
    "host_without_port",
    "https_url",
    "is_https",
    "is_local_request",
    "request_scheme",
]
