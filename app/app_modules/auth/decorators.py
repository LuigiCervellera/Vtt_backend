import jwt
import time
from collections import defaultdict
from functools import wraps
from quart import request, jsonify, g
from app.app_modules.base.config import JWT_SECRET, JWT_ALGORITHM
from app.app_modules.auth.blacklist import is_blacklisted


def jwt_required(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        token = request.cookies.get("vtt_token")
        if not token:
            # Fallback per strumenti di test ed API client tradizionali
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                
        if not token:
            return jsonify({"error": "Token mancante o non valido"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

            # Verifica che il token non sia stato revocato (logout)
            jti = payload.get("jti")
            if jti and await is_blacklisted(jti):
                return jsonify({"error": "Token revocato"}), 401
            
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token scaduto"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token non valido"}), 401
            
        return await f(*args, **kwargs)
    return decorated


# IP -> list of request timestamps
_rate_limit_records = defaultdict(list)

def rate_limit(limit: int, period: int = 60):
    """
    Decorator per applicare il rate limiting basato sull'IP del client.
    Limit: numero massimo di richieste consentite nel periodo (in secondi).
    """
    def decorator(f):
        @wraps(f)
        async def decorated(*args, **kwargs):
            ip = request.headers.get("X-Forwarded-For")
            if ip:
                ip = ip.split(",")[0].strip()
            else:
                ip = request.remote_addr or "127.0.0.1"

            now = time.time()
            # Clean expired timestamps
            timestamps = [t for t in _rate_limit_records[ip] if now - t < period]
            
            if len(timestamps) >= limit:
                return jsonify({"error": "Troppe richieste. Riprova più tardi."}), 429
            
            timestamps.append(now)
            _rate_limit_records[ip] = timestamps
            return await f(*args, **kwargs)
        return decorated
    return decorator

