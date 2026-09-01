from .production import *  # noqa: F403

# The simulator is intentionally reachable only through Railway's private HTTP
# network. It must not inherit the public portal URL configuration or redirect
# the worker to an HTTPS hostname that does not exist on the private network.
ROOT_URLCONF = "config.simulator_urls"
WSGI_APPLICATION = "config.simulator_wsgi.application"

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
