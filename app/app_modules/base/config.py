import os
import warnings
import sys
from dotenv import load_dotenv

# Carica le variabili dal file .env se presente
load_dotenv()


# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# CORS configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

# File uploads and limitations
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB
MAX_WS_MESSAGE_SIZE = 64 * 1024  # 64KB

# JWT configuration
_INSECURE_SECRETS = {
    "dev-only-insecure-secret-change-me",
    "CHANGE-ME-generate-a-secure-secret",
    "",
}

JWT_SECRET = os.getenv("JWT_SECRET", "")
if JWT_SECRET in _INSECURE_SECRETS:
    if os.getenv("FLASK_DEBUG") or os.getenv("QUART_DEBUG"):
        warnings.warn(
            "JWT_SECRET is insecure! Using fallback for development ONLY. "
            "Set a strong JWT_SECRET env var before deploying to production!",
            stacklevel=1
        )
        JWT_SECRET = "dev-only-insecure-secret-DO-NOT-USE-IN-PRODUCTION"
    else:
        print(
            "FATAL: JWT_SECRET is not set or uses an insecure default. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\" "
            "and set it in your .env file.",
            file=sys.stderr
        )
        sys.exit(1)

JWT_ALGORITHM = "HS256"
JWT_EXP_DELTA_SECONDS = 86400  # 24 hours

# Password & Username validation
PASSWORD_MIN_LENGTH = 8
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 50

# Cookie configuration for JWT storage
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "False").lower() in ("true", "1", "yes")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "Lax")


