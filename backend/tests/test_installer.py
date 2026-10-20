import json
from pathlib import Path

from app.modules import installer

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "deploy" / "nexus" / "installable.json"


def test_installable_catalog_is_valid_and_allowlisted():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert "nginx" in catalog
    assert "docker" in catalog
    for key, entry in catalog.items():
        assert installer.KEY_RE.match(key), key
        assert entry["packages"], key


def test_installer_rejects_unknown_keys_before_calling_sudo():
    result = installer.install("../nginx")
    assert result["success"] is False
