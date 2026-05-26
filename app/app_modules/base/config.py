import os
import warnings

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# CORS configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

# File uploads and limitations
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
MAX_WS_MESSAGE_SIZE = 64 * 1024  # 64KB

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    warnings.warn(
        "JWT_SECRET not set! Using insecure default. Set JWT_SECRET env var in production!",
        stacklevel=1
    )
    JWT_SECRET = "dev-only-insecure-secret-change-me"

JWT_ALGORITHM = "HS256"
JWT_EXP_DELTA_SECONDS = 86400 * 30  # 30 days
