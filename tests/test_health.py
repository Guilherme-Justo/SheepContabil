from django.test import Client
from django.urls import reverse


class UnavailableDatabase:
    def cursor(self) -> None:
        raise RuntimeError("database unavailable")


def test_liveness_does_not_require_authentication(client: Client) -> None:
    response = client.get(reverse("health-live"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_the_database(client: Client, db) -> None:
    response = client.get(reverse("health-ready"))

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_readiness_reports_database_failure(client: Client, monkeypatch) -> None:
    monkeypatch.setattr("config.health.connections", {"default": UnavailableDatabase()})

    response = client.get(reverse("health-ready"))

    assert response.status_code == 503
    assert response.json() == {"status": "not-ready", "database": "unavailable"}
