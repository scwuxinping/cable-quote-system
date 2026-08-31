import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_secret_key() -> str:
    """优先读环境变量；否则首次运行自动生成并保存到本地 secret.key。"""
    env_key = os.environ.get('DJANGO_SECRET_KEY')
    if env_key:
        return env_key
    key_file = BASE_DIR / 'secret.key'
    if key_file.exists():
        return key_file.read_text(encoding='utf-8').strip()
    key_file.write_text(secrets.token_urlsafe(50), encoding='utf-8')
    return key_file.read_text(encoding='utf-8').strip()


SECRET_KEY = _load_secret_key()
DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'quoter',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
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
        'DIRS': [BASE_DIR / 'templates'],
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

WSGI_APPLICATION = 'config.wsgi.application'

def _database_from_env():
    """默认 SQLite；设 DATABASE_URL=postgres://user:pwd@host:5432/dbname 切换 PostgreSQL。"""
    url = os.environ.get('DATABASE_URL', '')
    if url.startswith(('postgres://', 'postgresql://')):
        from urllib.parse import urlparse
        p = urlparse(url)
        return {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': p.path.lstrip('/'),
            'USER': p.username or '',
            'PASSWORD': p.password or '',
            'HOST': p.hostname or '',
            'PORT': str(p.port or 5432),
        }
    return {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }


DATABASES = {'default': _database_from_env()}

# 邮件发送（报价单邮件留痕功能）。以下环境变量配置后即启用，
# 例（QQ 企业邮箱）：EMAIL_HOST=smtp.exmail.qq.com EMAIL_HOST_USER=xxx@xx.com EMAIL_HOST_PASSWORD=***
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '465'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', '1') == '1'
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER or 'cable-quote@localhost'

# 企业微信群机器人通知（可选）：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
WECOM_WEBHOOK = os.environ.get('WECOM_WEBHOOK', '')

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# 报价单附件等（预留）
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
