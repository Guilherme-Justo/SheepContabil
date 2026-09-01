from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_simulator_wsgi_uses_private_routes_without_https_redirect() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(project_dir / "src"),
        "DJANGO_SETTINGS_MODULE": "config.settings.simulator",
        "DJANGO_SECRET_KEY": "test-simulator-key-8rX3pQ7vN2mK5tW9cL4sH6jF1aD0zB7y",
        "DJANGO_ALLOWED_HOSTS": "healthcheck.railway.app,simulator.railway.internal",
        "DATABASE_URL": "postgresql://ci:ci@127.0.0.1:5432/ci",
    }
    code = """
from config.simulator_wsgi import application
from django.conf import settings
from django.urls import resolve

assert application is not None
assert settings.ROOT_URLCONF == "config.simulator_urls"
assert settings.SECURE_SSL_REDIRECT is False
assert settings.SESSION_COOKIE_SECURE is False
assert settings.CSRF_COOKIE_SECURE is False
assert resolve("/login/").view_name == "sc05_simulator:login"
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_dir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
