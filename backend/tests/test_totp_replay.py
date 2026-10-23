"""Anti-replay de TOTP: un código aceptado no se puede reutilizar.

El test usa una base de datos aislada (monkeypatch de ``security.db_path``)
para no interferir con el administrador de la suite principal.
"""

from __future__ import annotations

from app.core import security, totp


def test_totp_replay_rejected(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "auth.db"
    monkeypatch.setattr(security, "db_path", lambda: db_file)

    security.init_db()
    security.create_admin("admin", "contrasena-de-prueba-123")
    secret = totp.generate_secret()
    security.stage_totp("admin", secret, [])
    security.enable_totp("admin")

    now = 1_700_000_000.0  # instante fijo: no depende del reloj real
    code = totp.totp_code(secret, now)

    first = security.verify_second_factor("admin", code, now=now)
    replay = security.verify_second_factor("admin", code, now=now)
    previous = security.verify_second_factor(
        "admin", totp.totp_code(secret, now - 30), now=now
    )
    following = security.verify_second_factor(
        "admin", totp.totp_code(secret, now + 30), now=now
    )

    assert first["ok"] is True
    assert replay["ok"] is False and replay["reason"] == "replay"
    assert previous["ok"] is False and previous["reason"] == "replay"
    assert following["ok"] is True
