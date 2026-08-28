import {
  bucket,
  defineRailway,
  github,
  postgres,
  preserve,
  project,
  redis,
  ref,
  service,
} from "railway/iac";

export default defineRailway(() => {
  const database = postgres("Postgres", { region: "us-east4-eqdc4a" });
  const broker = redis("Redis", { region: "us-east4-eqdc4a" });
  const artifacts = bucket("sheepcontabil-artifacts", { region: "iad" });

  const commonEnvironment = {
    DJANGO_SETTINGS_MODULE: "config.settings.production",
    DJANGO_SECRET_KEY: preserve(),
    DJANGO_ALLOWED_HOSTS: "healthcheck.railway.app",
    APP_TIME_ZONE: "America/Sao_Paulo",
    DATABASE_URL: database.env.DATABASE_URL,
    REDIS_URL: broker.env.REDIS_URL,
    S3_ENDPOINT_URL: ref(artifacts, "ENDPOINT"),
    S3_ACCESS_KEY_ID: ref(artifacts, "ACCESS_KEY_ID"),
    S3_SECRET_ACCESS_KEY: ref(artifacts, "SECRET_ACCESS_KEY"),
    S3_BUCKET_NAME: ref(artifacts, "BUCKET"),
    S3_REGION: ref(artifacts, "REGION"),
  };

  const web = service("web", {
    source: github("Guilherme-Justo/SheepContabil", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start:
      '/bin/sh -c "exec gunicorn config.wsgi:application --chdir src --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --timeout 120 --access-logfile - --error-logfile -"',
    preDeploy: "python src/manage.py migrate --noinput",
    healthcheck: "/health/ready",
    healthcheckTimeout: 300,
    replicas: { "us-east4-eqdc4a": 1 },
    env: {
      ...commonEnvironment,
      DEMO_ADMIN_PASSWORD: preserve(),
      DEMO_OPERATOR_PASSWORD: preserve(),
    },
  });

  const worker = service("worker", {
    source: github("Guilherme-Justo/SheepContabil", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "celery --app config worker --loglevel INFO --concurrency 1",
    replicas: { "us-east4-eqdc4a": 1 },
    env: {
      ...commonEnvironment,
      OPENAI_API_KEY: preserve(),
      OPENAI_MODEL: "gpt-5.4-mini-2026-03-17",
    },
  });

  return project("SheepContabil", {
    resources: [database, broker, artifacts, web, worker],
  });
});
