"""Owner-only production defaults. Never commit real secrets."""
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DEBUG", "0") == "1"
def _read_secret_file(variable):
    """Read the stable secret from a mounted, non-versioned file."""
    path = os.environ.get(variable, "")
    if not path:
        return ""
    raw = Path(path).read_text(encoding="utf-8").rstrip("\r\n")
    if not raw:
        raise RuntimeError("%s points to an empty file" % variable)
    return raw


SECRET_KEY = _read_secret_file("SECRET_KEY_FILE") or os.environ.get("SECRET_KEY", "")
# Optional separate encryption key; absent on old deployments until explicit upgrade.
DATA_ENCRYPTION_KEY = (_read_secret_file("DATA_ENCRYPTION_KEY_FILE")
                       or os.environ.get("DATA_ENCRYPTION_KEY", ""))
# Keys retired by a rotation: still used to read old records until `migrate_data_encryption` re-encrypts.
DATA_ENCRYPTION_KEYS_PREVIOUS = [k.strip() for k in (
    _read_secret_file("DATA_ENCRYPTION_KEYS_PREVIOUS_FILE")
    or os.environ.get("DATA_ENCRYPTION_KEYS_PREVIOUS", "")).split(",") if k.strip()]
# Most state-changing requests one signed-in user may make per minute (0 turns the limit off).
WRITE_RATE_LIMIT_PER_MINUTE = int(os.environ.get("WRITE_RATE_LIMIT_PER_MINUTE", "120") or 0)
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("SECRET_KEY or SECRET_KEY_FILE is required when DEBUG=0")
    SECRET_KEY = "dev-only-not-for-production-do-not-deploy"
ALLOWED_HOSTS = [s.strip() for s in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if s.strip()]
CSRF_TRUSTED_ORIGINS = [s.strip() for s in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if s.strip()]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "tenancy",
    "tracker",
]
MIDDLEWARE = [
    "tenancy.ops.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "tracker.security_headers.RestrictiveResponseHeaders",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tracker.totp_middleware.RequireOwnerTOTP",
    "tenancy.middleware.TenantMiddleware",
    "tenancy.ratelimit.WriteRateLimit",
    "tracker.seo.SeoMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "wealtharr.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug",
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "tracker.context_processors.chrome",
        "tracker.context_processors.public_site",
        "tenancy.context_processors.signup",
    ]},
}]
WSGI_APPLICATION = "wealtharr.wsgi.application"
ASGI_APPLICATION = "wealtharr.asgi.application"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_PASSWORD = _read_secret_file("DATABASE_PASSWORD_FILE")
if DATABASE_PASSWORD:
    # File-backed DB credentials override an obsolete URL, so punctuation in
    # random passwords cannot corrupt URL parsing. The file is never logged.
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DATABASE_NAME", "wealtharr"),
        "USER": os.environ.get("DATABASE_USER", "wealtharr"),
        "PASSWORD": DATABASE_PASSWORD,
        "HOST": os.environ.get("DATABASE_HOST", "db"),
        "PORT": os.environ.get("DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}
elif DATABASE_URL:
    u = urlparse(DATABASE_URL)
    if u.scheme not in ("postgres", "postgresql", "sqlite"):
        raise RuntimeError("Only PostgreSQL or SQLite database URLs are supported")
    if u.scheme == "sqlite":
        if not DEBUG:
            raise RuntimeError("SQLite is for development only; production requires PostgreSQL")
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": unquote(u.path)}}
    else:
        DATABASES = {"default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(u.path.lstrip("/")),
            "USER": unquote(u.username or ""),
            "PASSWORD": unquote(u.password or ""),
            "HOST": u.hostname or "localhost",
            "PORT": str(u.port or 5432),
            "CONN_MAX_AGE": 60,
        }}
elif DEBUG:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}
else:
    raise RuntimeError("PostgreSQL DATABASE_PASSWORD_FILE or DATABASE_URL is required when DEBUG=0")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Project-owned CSS/JS lives in BASE_DIR/static, outside installed apps.
# Without this, collectstatic only copies Django admin assets and production
# manifest lookups for css/site.css and js/site.js cause HTTP 500.
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"staticfiles": {"BACKEND": (
    "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/overview/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Proxies in front of the app that each append the address they saw (see
# tracker/client_ip.py). 0 trusts none: per-visitor rate limits then stay off.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0") or 0)
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_AGE = 60 * 60 * 12
if not DEBUG and ("*" in ALLOWED_HOSTS or not ALLOWED_HOSTS):
    raise RuntimeError("Production ALLOWED_HOSTS must enumerate the private hostname")
X_FRAME_OPTIONS = "DENY"
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
# The one public origin search engines should index, e.g. https://www.example.com.
# When set, every other host 301-redirects to it; add both hosts to ALLOWED_HOSTS.
# Database row-level security (see docs/saas/RLS.md). Only on after `manage.py rls_apply`.
RLS_ENABLED = os.environ.get("RLS_ENABLED", "0") == "1"
# Public registration. Off: accounts are still created by hand in the admin.
SELF_SERVE_SIGNUP = os.environ.get("SELF_SERVE_SIGNUP", "0") == "1"
# Ask each person to confirm they chose their strategy themselves when adding one (off by default).
REQUIRE_CHOICE_ACK = os.environ.get("REQUIRE_CHOICE_ACK", "0") == "1"
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")
# Shown on the privacy page for access and deletion requests; optional.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "").strip()
# Stripe signs webhook calls with this; leave empty and the webhook endpoint stays off.
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")
# Production login intentionally fails closed if Turnstile is not configured.
TURNSTILE_REQUIRED = not DEBUG

# Logs: plain text by default, one JSON object per line with LOG_FORMAT=json (each tagged with the request id).
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "tenancy.ops.RequestIdFilter"}},
    "formatters": {
        "json": {"()": "tenancy.ops.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["request_id"],
                             "formatter": "json" if os.environ.get("LOG_FORMAT") == "json" else "plain"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
