import os
from pathlib import Path
from urllib.parse import urlparse

# --- Chemins de base ---
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Clé secrète Django ---
SECRET_KEY = os.environ.get("SECRET_KEY", "qhO106Ry4B")  # Pour tests locaux uniquement

# --- Mode DEBUG ---
DEBUG = True  # ⚠️ Passe à False en production
ALLOWED_HOSTS = ["gestionabsences.azurewebsites.net", "127.0.0.1", "localhost"]

# --- Applications Django ---
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'whitenoise.runserver_nostatic',
    'absences',
    'storages',  # pour Azure Storage
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles') 

ROOT_URLCONF = 'gestion_absencesApp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'gestion_absencesApp.wsgi.application'

# --- Base de données PostgreSQL sur Azure ---
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'gestionabsencebd',
        'USER': 'adminazure',
        'PASSWORD': '94649092Hiy',
        'HOST': 'gestionabsencesapp-public.postgres.database.azure.com',
        'PORT': '5432',
    }
}

# --- Sécurité mot de passe ---
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --- Internationalisation ---
LANGUAGE_CODE = 'fr'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# --- Authentification / Redirection ---
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard_collaborateur'
LOGOUT_REDIRECT_URL = 'login'

# --- Clé par défaut pour les modèles ---
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Azure Blob Storage (fichiers médias) ---
AZURE_ACCOUNT_NAME = os.environ.get("AZURE_ACCOUNT_NAME")
AZURE_ACCOUNT_KEY = os.environ.get("AZURE_ACCOUNT_KEY")  
AZURE_CONTAINER = os.environ.get("AZURE_CONTAINER", "media")
AZURE_CUSTOM_DOMAIN = f"{AZURE_ACCOUNT_NAME}.blob.core.windows.net"

DEFAULT_FILE_STORAGE = "storages.backends.azure_storage.AzureStorage"
MEDIA_URL = f"https://{AZURE_CUSTOM_DOMAIN}/{AZURE_CONTAINER}/"


# Note : MEDIA_ROOT n'est plus utilisé, tout passe par Azure Blob
