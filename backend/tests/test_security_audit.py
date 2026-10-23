"""Tests de la auditoría de seguridad de solo lectura."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.modules import security_audit


def test_security_audit_shape() -> None:
    data = security_audit.run_audit(force=True)
    assert data["read_only"] is True
    assert 0 <= int(data["score"]) <= 100
    assert isinstance(data["findings"], list) and data["findings"]
    assert data["counts"]["total"] == len(data["findings"])

    ids = [item["id"] for item in data["findings"]]
    assert len(ids) == len(set(ids)), "los IDs de los checks deben ser únicos"

    for item in data["findings"]:
        assert item["severity"] in security_audit.SEVERITIES
        assert item["status"] in security_audit.STATUSES


def test_security_audit_api(client: TestClient, csrf: str) -> None:
    response = client.get("/api/audit/security")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["findings"]

    forced = client.post(
        "/api/audit/security/run",
        headers={"X-CSRF-Token": csrf},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["read_only"] is True


def _finding(check_id: str, status: str, severity: str = "high") -> dict:
    return {
        "id": check_id,
        "category": "Test",
        "title": check_id,
        "severity": severity,
        "status": status,
        "evidence": "e",
        "recommendation": "r",
        "reference": "",
    }


def _report(findings: list[dict], score: int = 80) -> dict:
    return {
        "read_only": True,
        "generated_at": "2026-10-23T00:00:00+00:00",
        "host": "test",
        "platform": "test",
        "score": score,
        "counts": {"total": len(findings)},
        "findings": findings,
        "duration_ms": 1,
        "cached": False,
    }


def test_compare_baseline() -> None:
    """Nuevos, resueltos, regresiones y los que siguen fallando."""
    current = _report(
        [_finding("A", "pass"), _finding("B", "warn", "medium"), _finding("D", "fail")],
        score=85,
    )
    baseline = {
        "saved_at": "2026-10-22T00:00:00+00:00",
        "score": 70,
        "findings": [
            _finding("A", "fail"),
            _finding("B", "warn", "medium"),
            _finding("C", "pass", "low"),
        ],
    }
    comparison = security_audit.compare(current, baseline)
    assert comparison is not None
    assert comparison["score_delta"] == 15
    assert [item["id"] for item in comparison["new_actionable"]] == ["D"]
    assert [item["id"] for item in comparison["fixed"]] == ["A"]
    assert [item["id"] for item in comparison["still_failing"]] == ["B"]
    assert [item["id"] for item in comparison["resolved"]] == ["C"]


def test_baseline_api(client: TestClient, csrf: str, monkeypatch) -> None:
    """Guardar, comparar y borrar el baseline sin ejecutar el host real."""
    synthetic = _report([_finding("A", "fail"), _finding("B", "pass")], score=80)
    monkeypatch.setattr(
        "app.api.routes_audit.run_audit",
        lambda force=False: dict(synthetic),
    )
    try:
        saved = client.post(
            "/api/audit/security/baseline",
            headers={"X-CSRF-Token": csrf},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["ok"] is True

        current = client.get("/api/audit/security")
        assert current.status_code == 200, current.text
        comparison = current.json()["comparison"]
        assert comparison is not None
        assert comparison["score_delta"] == 0
        assert comparison["new_actionable"] == []

        removed = client.delete(
            "/api/audit/security/baseline",
            headers={"X-CSRF-Token": csrf},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["removed"] is True
    finally:
        security_audit.delete_baseline()

