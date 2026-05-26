import jwt
from functools import wraps
from quart import request, jsonify, g
from app.app_modules.base.config import JWT_SECRET, JWT_ALGORITHM
from app.app_modules.auth.blacklist import is_blacklisted


def jwt_required(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token mancante o non valido"}), 401
        
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

            # Verifica che il token non sia stato revocato (logout)
            jti = payload.get("jti")
            if jti and is_blacklisted(jti):
                return jsonify({"error": "Token revocato"}), 401
            
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token scaduto"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token non valido"}), 401
            
        return await f(*args, **kwargs)
    return decorated

