import {
  bucket,
  defineRailway,
  fn,
  github,
  postgres,
  preserve,
  project,
  redis,
  ref,
  service,
} from "railway/iac";

export default defineRailway(() => {
  const database = postgres("Postgres", { region: "ams" });
  const broker = redis("Redis", { region: "ams" });
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
    S3_ADDRESSING_STYLE: "path",
    SC04_AUTO_ROUTE_THRESHOLD: "0.85",
    SC04_DAILY_HOUR: "8",
  };

  const web = service("web", {
    source: github("Guilherme-Justo/SheepContabil", {
      branch: "main",
      checkSuites: true,
    }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start:
      '/bin/sh -c "exec gunicorn config.wsgi:application --chdir src --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --timeout 120 --access-logfile - --error-logfile -"',
    preDeploy: "sh scripts/predeploy.sh",
    healthcheck: "/health/ready",
    healthcheckTimeout: 300,
    replicas: { "us-east4-eqdc4a": 1 },
    env: {
      ...commonEnvironment,
      DEMO_ADMIN_PASSWORD: preserve(),
      DEMO_OPERATOR_PASSWORD: preserve(),
      DEMO_SOCIETARY_OPERATOR_PASSWORD: preserve(),
      DEMO_FISCAL_OPERATOR_PASSWORD: preserve(),
      DEMO_TECHNOLOGY_OPERATOR_PASSWORD: preserve(),
      SEED_DEMO_ON_DEPLOY: preserve(),
    },
  });

  const simulator = service("simulator", {
    source: github("Guilherme-Justo/SheepContabil", {
      branch: "main",
      checkSuites: true,
    }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start:
      '/bin/sh -c "exec gunicorn config.simulator_wsgi:application --chdir src --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 120 --access-logfile - --error-logfile -"',
    healthcheck: "/health/ready",
    healthcheckTimeout: 300,
    replicas: { "us-east4-eqdc4a": 1 },
    env: {
      DJANGO_SETTINGS_MODULE: "config.settings.simulator",
      DJANGO_SECRET_KEY: preserve(),
      DJANGO_ALLOWED_HOSTS: "healthcheck.railway.app,simulator.railway.internal,simulator",
      APP_TIME_ZONE: "America/Sao_Paulo",
      DATABASE_URL: database.env.DATABASE_URL,
      PORT: "8000",
      SC05_SIMULATOR_USERNAME: preserve(),
      SC05_SIMULATOR_PASSWORD: preserve(),
    },
  });

  const worker = service("worker", {
    source: github("Guilherme-Justo/SheepContabil", {
      branch: "main",
      checkSuites: true,
    }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "celery --app config worker --loglevel INFO --concurrency 1",
    replicas: { "us-east4-eqdc4a": 1 },
    env: {
      ...commonEnvironment,
      OPENAI_API_KEY: preserve(),
      OPENAI_MODEL: preserve(),
      SC05_SIMULATOR_BASE_URL: "http://simulator.railway.internal:8000",
      SC05_SIMULATOR_USERNAME: simulator.env.SC05_SIMULATOR_USERNAME,
      SC05_SIMULATOR_PASSWORD: simulator.env.SC05_SIMULATOR_PASSWORD,
      SC05_RPA_TIMEOUT_MS: "10000",
    },
  });

  const scheduler = fn("scheduler", {
    source: github("Guilherme-Justo/SheepContabil", {
      branch: "main",
      checkSuites: true,
    }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "python src/manage.py dispatch_due_schedules",
    deploy: {
      cronSchedule: "*/15 * * * *",
      restartPolicyType: "NEVER",
    },
    replicas: { "us-east4-eqdc4a": 1 },
    env: {
      DJANGO_SETTINGS_MODULE: "config.settings.production",
      DJANGO_SECRET_KEY: web.env.DJANGO_SECRET_KEY,
      DJANGO_ALLOWED_HOSTS: "healthcheck.railway.app",
      APP_TIME_ZONE: "America/Sao_Paulo",
      DATABASE_URL: database.env.DATABASE_URL,
      REDIS_URL: broker.env.REDIS_URL,
    },
  });

  return project("SheepContabil", {
    resources: [database, broker, artifacts, web, simulator, worker, scheduler],
  });
});
