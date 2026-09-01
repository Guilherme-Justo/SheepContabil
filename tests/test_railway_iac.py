import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_worker_colocates_sc05_simulator_without_extra_railway_service() -> None:
    railway_config = (PROJECT_ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")

    assert re.search(
        r"start\s*:\s*['\"]sh scripts/run_worker_with_simulator\.sh['\"]",
        railway_config,
    )
    assert re.search(
        r"SC05_SIMULATOR_BASE_URL\s*:\s*['\"]http://127\.0\.0\.1:8000['\"]",
        railway_config,
    )
    assert re.search(r"SC05_SIMULATOR_DJANGO_SECRET_KEY\s*:\s*preserve\(\)", railway_config)
    assert re.search(r"drainingSeconds\s*:\s*300", railway_config)
    assert re.search(r"healthcheck\s*:\s*['\"]/health/ready['\"]", railway_config)
    assert not re.search(r"service\s*\(\s*['\"]simulator['\"]", railway_config, re.IGNORECASE)
