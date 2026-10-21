from pathlib import Path
import os
from dotenv import load_dotenv
import sentry_sdk

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

_sentry_dsn = os.getenv('SENTRY_DSN')
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        send_default_pii=True,
        enable_logs=True,
        traces_sample_rate=0.2,
    )

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# Defaults to FALSE. It used to default to True, which is how production
# served Django's technical 500 page -- including the DATABASES dict, with the
# Neon host and username unredacted (Django only hides settings whose NAMES
# match KEY/SECRET/PASS/TOKEN) and 14 stack frames of local variables. Local
# development sets DEBUG=True explicitly; see backend/.env.example.
DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

_db_url = os.getenv('DATABASE_URL', '')
if _db_url.startswith('postgres'):
    import urllib.parse as _up
    _r = _up.urlparse(_db_url)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': _r.path.lstrip('/'),
            'USER': _r.username,
            'PASSWORD': _r.password,
            'HOST': _r.hostname,
            'PORT': _r.port or 5432,
        }
    }
elif not DEBUG:
    # Fail loudly rather than silently falling back to SQLite.
    #
    # On Render the filesystem is ephemeral, so a missing or malformed
    # DATABASE_URL used to mean the app booted happily against a throwaway
    # db.sqlite3 and quietly lost every user's data on the next deploy. A
    # misconfigured production database must stop the boot, not hide.
    raise RuntimeError(
        'DATABASE_URL must be a postgres:// URL when DEBUG is False. '
        'Got %r. Refusing to start on ephemeral SQLite in production.'
        % (_db_url[:30] + '...' if len(_db_url) > 30 else _db_url)
    )
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# All uploaded files (body/food scans) are handled in memory — never written to disk
FILE_UPLOAD_HANDLERS = ['django.core.files.uploadhandler.MemoryFileUploadHandler']
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20MB (default 2.5MB is too small for iPhone photos)
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'api.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        # Session credential: one row per user, deleted by logout.
        'rest_framework.authentication.TokenAuthentication',
        # Long-lived integration credential, independent of sessions and
        # scoped. See api/auth_keys.py.
        'api.auth_keys.ApiKeyAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
        # A no-op unless the request authenticated with an ApiKey, so the web
        # app is unaffected. Applied globally on purpose: scoping enforced
        # per-view would leave every newly added endpoint open by default.
        'api.auth_keys.ApiKeyScopePermission',
    ],
}

CORS_ALLOWED_ORIGINS = os.getenv(
    'CORS_ALLOWED_ORIGINS', 'http://localhost:5173'
).split(',')

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
FROM_EMAIL = os.getenv('FROM_EMAIL', 'GetFit <noreply@resend.dev>')
TERRA_API_KEY = os.getenv('TERRA_API_KEY', '')
TERRA_DEV_ID = os.getenv('TERRA_DEV_ID', '')
