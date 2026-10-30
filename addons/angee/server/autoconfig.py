"""HTTP defaults activate only through an enabled serving capability."""

SETTINGS = {
    "ROOT_URLCONF": "django_compose.urls",
    "ASGI_APPLICATION": "angee.server.application",
    "MIDDLEWARE:prepend": ["django.middleware.security.SecurityMiddleware"],
    "MIDDLEWARE:append": [
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csp.ContentSecurityPolicyMiddleware",
    ],
    "ANGEE_WEB_STATEMENT_TIMEOUT": 60,
}
