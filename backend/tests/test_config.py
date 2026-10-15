"""Tests de la edición guiada de configuración (Fases 3/4)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.modules import config_manager
from app.schemas.models import DnsmasqConfig, SambaConfig, SambaShare


def test_render_samba_contains_share() -> None:
    """El render de Samba incluye marca, sección y parámetros de la share."""
    config = SambaConfig(
        shares=[
            SambaShare(name="datos", path="/srv/datos", comment="Datos", read_only=True)
        ]
    )
    text = config_manager.render_samba(config)
    assert config_manager.MARKER in text
    assert "[datos]" in text
    assert "path = /srv/datos" in text
    assert "read only = yes" in text


def test_render_dnsmasq_contains_all_sections() -> None:
    """El render de dnsmasq incluye DNS, registros, DHCP y reservas."""
    config = DnsmasqConfig(
        servers=["9.9.9.9"],
        records=[{"name": "nas.local", "address": "192.168.1.10"}],
        dhcp={
            "enabled": True,
            "start": "192.168.1.100",
            "end": "192.168.1.150",
            "lease_time": "12h",
        },
        reservations=[
            {"mac": "AA:BB:CC:DD:EE:FF", "address": "192.168.1.20", "hostname": "printer"}
        ],
    )
    text = config_manager.render_dnsmasq(config)
    assert "server=9.9.9.9" in text
    assert "address=/nas.local/192.168.1.10" in text
    assert "dhcp-range=192.168.1.100,192.168.1.150,12h" in text
    assert "dhcp-host=aa:bb:cc:dd:ee:ff,192.168.1.20,printer" in text


def test_roundtrip_samba() -> None:
    """Render -> parse conserva las shares."""
    original = SambaConfig(
        shares=[SambaShare(name="pub", path="/srv/pub", guest_ok=True)]
    )
    parsed = config_manager._parse_smb(config_manager.render_samba(original))
    assert parsed.shares[0].name == "pub"
    assert parsed.shares[0].path == "/srv/pub"
    assert parsed.shares[0].guest_ok is True


def test_share_rejects_bad_path() -> None:
    """Rutas relativas o con '..' se rechazan."""
    with pytest.raises(Exception):
        SambaShare(name="x", path="relative/path")
    with pytest.raises(Exception):
        SambaShare(name="x", path="/srv/../etc")


def test_share_rejects_injection_name() -> None:
    """Nombres con caracteres peligrosos se rechazan."""
    with pytest.raises(Exception):
        SambaShare(name="bad;name", path="/srv/x")


def test_dhcp_range_incoherent() -> None:
    """Un rango DHCP invertido se rechaza."""
    with pytest.raises(Exception):
        DnsmasqConfig(
            dhcp={"enabled": True, "start": "192.168.1.200", "end": "192.168.1.100"}
        )


def test_validate_config_dev_mode() -> None:
    """La validación genera borrador aunque no haya herramientas nativas."""
    result = config_manager.validate_config("samba", SambaConfig())
    assert result["preview"]
    assert isinstance(result["ok"], bool)


def test_save_config_with_helper(monkeypatch) -> None:
    """Con helpers disponibles, el guardado llama a conf-apply (simulado)."""
    monkeypatch.setattr(config_manager, "privileged_available", lambda: True)
    monkeypatch.setattr(
        config_manager,
        "run_helper",
        lambda name, args, timeout=45.0: {
            "success": True,
            "code": 0,
            "output": "OK simulado",
        },
    )
    result = config_manager.save_config("samba", SambaConfig(), "tester")
    assert result["success"] is True
    assert result["message"] == "OK simulado"


def test_restore_rejects_bad_name() -> None:
    """Un nombre de copia con ruta se rechaza."""
    result = config_manager.restore_backup("samba", "../../etc/passwd", "tester")
    assert result["success"] is False


def test_config_api_flow(client: TestClient, csrf: str) -> None:
    """Flujo completo de la API de configuración."""
    response = client.get("/api/config/samba")
    assert response.status_code == 200
    body = response.json()
    assert "config" in body and "managed" in body

    payload = {"config": {"shares": [{"name": "test", "path": "/srv/test"}]}}

    response = client.post(
        "/api/config/samba/validate", json=payload, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["preview"]

    response = client.put(
        "/api/config/samba", json=payload, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json()["success"] is False  # sin helpers en este sistema

    response = client.get("/api/config/samba/backups")
    assert response.status_code == 200
    assert "backups" in response.json()

    response = client.get("/api/config/dnsmasq")
    assert response.status_code == 200
    assert response.json()["config"]["dhcp"]["enabled"] is False

    response = client.post("/api/config/dnsmasq/dhcp-check", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert "available" in response.json()

    response = client.put(
        "/api/config/samba",
        json={"config": {"shares": [{"name": "mal;nombre", "path": "/srv/x"}]}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
