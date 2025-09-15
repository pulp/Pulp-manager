CONTENT_ORIGIN = "http://localhost"

# Database settings
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'pulp',
        'USER': 'pulp',
        'PASSWORD': 'password',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    }
}

# Cache settings for performance
CACHE_ENABLED = True

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "pulp: %(name)s:%(levelname)s: %(message)s"}
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple"
        }
    },
    "loggers": {
        "": {
            "handlers": ["console"],
            "level": "INFO"
        }
    }
}

# Task settings
RQ_QUEUES = {
    'default': {
        'HOST': 'localhost',
        'PORT': 6379,
        'DB': 0,
        'DEFAULT_TIMEOUT': 360,
    },
}

# Enable package signing
PACKAGE_SIGNING_SERVICE = True

# Token authentication
TOKEN_AUTH_DISABLED = False

# Content settings
MEDIA_ROOT = "/var/lib/pulp/"

# Default admin user
PULP_DEFAULT_ADMIN_PASSWORD = "password"
# Signing service configuration
SIGNING_SERVICES = {
    'deb_signing_service': {
        'SCRIPT': '/opt/deb_sign_script.sh',
    }
}
