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


def test_scheduler_receives_explicit_schedule_hours() -> None:
    railway_config = (PROJECT_ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")
    scheduler_match = re.search(
        r"const scheduler = fn\(.*?(?=\n\s*return project)",
        railway_config,
        re.DOTALL,
    )

    assert scheduler_match is not None
    scheduler_config = scheduler_match.group()
    for expected_setting in (
        r'DJANGO_SETTINGS_MODULE\s*:\s*"config\.settings\.production"',
        r"DJANGO_SECRET_KEY\s*:\s*web\.env\.DJANGO_SECRET_KEY",
        r'APP_TIME_ZONE\s*:\s*"America/Sao_Paulo"',
        r"DATABASE_URL\s*:\s*database\.env\.DATABASE_URL",
        r"REDIS_URL\s*:\s*broker\.env\.REDIS_URL",
        r'CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS\s*:\s*"1200"',
        r'AUTOMATION_QUEUED_STALE_AFTER_SECONDS\s*:\s*"1800"',
        r'AUTOMATION_RUNNING_STALE_AFTER_SECONDS\s*:\s*"1800"',
        r'AUTOMATION_RECONCILIATION_MAX_ATTEMPTS\s*:\s*"1"',
        r'SC04_DAILY_HOUR\s*:\s*"8"',
        r'SC20_MONTHLY_HOUR\s*:\s*"8"',
    ):
        assert re.search(expected_setting, scheduler_config)
    for unrelated_setting in (
        "S3_",
        "OPENAI_",
        "EMAIL_",
        "SC20_NOTIFICATION_BACKEND",
        "SC20_EMAIL_OVERRIDE_TO",
        "SC20_WHATSAPP_OVERRIDE_TO",
        "DEMO_",
        "SC05_",
    ):
        assert unrelated_setting not in scheduler_config


def test_web_and_worker_share_the_reconciliation_safety_settings() -> None:
    railway_config = (PROJECT_ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")
    common_match = re.search(
        r"const commonEnvironment = \{.*?\n\s*\};",
        railway_config,
        re.DOTALL,
    )

    assert common_match is not None
    common_config = common_match.group()
    for expected_setting in (
        r'CELERY_BROKER_VISIBILITY_TIMEOUT_SECONDS\s*:\s*"1200"',
        r'AUTOMATION_QUEUED_STALE_AFTER_SECONDS\s*:\s*"1800"',
        r'AUTOMATION_RUNNING_STALE_AFTER_SECONDS\s*:\s*"1800"',
        r'AUTOMATION_RECONCILIATION_MAX_ATTEMPTS\s*:\s*"1"',
    ):
        assert re.search(expected_setting, common_config)
    assert railway_config.count("...commonEnvironment") == 2


def test_application_services_share_one_current_region() -> None:
    railway_config = (PROJECT_ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")

    assert re.search(r'const APPLICATION_REGION\s*=\s*["\']us-east4-eqdc4a["\'];', railway_config)
    assert railway_config.count("replicas: { [APPLICATION_REGION]: 1 }") == 3
    assert railway_config.count('replicas: { "us-east4-eqdc4a": 1 }') == 0


def test_all_application_services_follow_main_after_ci() -> None:
    railway_config = (PROJECT_ROOT / ".railway" / "railway.ts").read_text(encoding="utf-8")
    source_blocks = re.findall(
        r'github\("Guilherme-Justo/SheepContabil",\s*\{(.*?)\}\)',
        railway_config,
        re.DOTALL,
    )

    assert len(source_blocks) == 3
    for source_block in source_blocks:
        assert re.search(r'branch\s*:\s*["\']main["\']', source_block)
        assert re.search(r"checkSuites\s*:\s*true", source_block)
