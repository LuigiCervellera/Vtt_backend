import re
from quart import Blueprint, jsonify, request, g, make_response
from quart_schema import validate_request, tag
import jwt
import uuid
import datetime
from models import User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.auth.schemas import AuthRequest, UpdateUsernameRequest, UpdatePasswordRequest
from app.app_modules.auth.blacklist import blacklist_token
from app.app_modules.base.config import (
    JWT_EXP_DELTA_SECONDS, JWT_SECRET, JWT_ALGORITHM,
    PASSWORD_MIN_LENGTH, USERNAME_MIN_LENGTH, USERNAME_MAX_LENGTH,
    COOKIE_SECURE, COOKIE_SAMESITE,
)

# Regex: solo lettere, numeri e underscore
_USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]+$")

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@auth_bp.route("/register", methods=["POST"])
@tag(["auth"])
@validate_request(AuthRequest)
async def register(data: AuthRequest):
    """Registrazione nuovo utente"""
    username = data.username.strip()
    password = data.password

    if not username or not password:
        return jsonify({"error": "Username e password richiesti"}), 400

    # Validazione username
    if len(username) < USERNAME_MIN_LENGTH or len(username) > USERNAME_MAX_LENGTH:
        return jsonify({"error": f"L'username deve essere tra {USERNAME_MIN_LENGTH} e {USERNAME_MAX_LENGTH} caratteri"}), 400
    if not _USERNAME_REGEX.match(username):
        return jsonify({"error": "L'username può contenere solo lettere, numeri e underscore"}), 400

    # Validazione password
    if len(password) < PASSWORD_MIN_LENGTH:
        return jsonify({"error": f"La password deve essere di almeno {PASSWORD_MIN_LENGTH} caratteri"}), 400

    existing_user = await User.get_or_none(username=username)
    if existing_user:
        return jsonify({"error": "Username già in uso"}), 409

    user = User(username=username)
    user.set_password(password)
    await user.save()

    return jsonify({"message": "Utente registrato con successo", "user": {"id": user.id, "username": user.username}}), 201

@auth_bp.route("/login", methods=["POST"])
@tag(["auth"])
@validate_request(AuthRequest)
async def login(data: AuthRequest):
    """Login utente"""
    username = data.username
    password = data.password

    if not username or not password:
        return jsonify({"error": "Username e password richiesti"}), 400

    user = await User.get_or_none(username=username)
    if user and user.check_password(password):
        payload = {
            "id": user.id,
            "username": user.username,
            "jti": str(uuid.uuid4()),  # ID univoco per revoca token
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=JWT_EXP_DELTA_SECONDS)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        resp_obj = await make_response(jsonify({
            "message": "Login effettuato", 
            "user": {"id": user.id, "username": user.username}
        }))
        resp_obj.set_cookie(
            "vtt_token",
            token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            max_age=JWT_EXP_DELTA_SECONDS
        )
        return resp_obj, 200
    
    return jsonify({"error": "Credenziali non valide"}), 401

@auth_bp.route("/me", methods=["GET"])
@tag(["auth"])
@jwt_required
async def get_me():
    """Restituisce l'utente corrente in base al JWT"""
    user = await User.get_or_none(id=g.user["id"])
    if not user:
        return jsonify({"error": "Utente non trovato"}), 404
    return jsonify({"user": {"id": user.id, "username": user.username}}), 200

@auth_bp.route("/logout", methods=["POST"])
@tag(["auth"])
@jwt_required
async def logout():
    """Logout utente (revoca token tramite blacklist JTI)"""
    jti = g.user.get("jti")
    if jti:
        blacklist_token(jti)
    resp_obj = await make_response(jsonify({"message": "Logout effettuato con successo"}))
    resp_obj.delete_cookie("vtt_token")
    return resp_obj, 200

@auth_bp.route("/update_username", methods=["PUT"])
@tag(["auth"])
@jwt_required
@validate_request(UpdateUsernameRequest)
async def update_username(data: UpdateUsernameRequest):
    """Modifica username"""
    user = await User.get_or_none(id=g.user["id"])
    
    if not user or not user.check_password(data.current_password):
        return jsonify({"error": "Credenziali attuali non valide"}), 401

    new_username = data.new_username.strip()
    if len(new_username) < USERNAME_MIN_LENGTH or len(new_username) > USERNAME_MAX_LENGTH:
        return jsonify({"error": f"L'username deve essere tra {USERNAME_MIN_LENGTH} e {USERNAME_MAX_LENGTH} caratteri"}), 400
    if not _USERNAME_REGEX.match(new_username):
        return jsonify({"error": "L'username può contenere solo lettere, numeri e underscore"}), 400

    if new_username == user.username:
        return jsonify({"error": "Il nuovo username deve essere diverso dal precedente"}), 400

    existing = await User.get_or_none(username=new_username)
    if existing:
        return jsonify({"error": "Il nuovo username è già in uso"}), 409
        
    user.username = new_username
    await user.save()
    return jsonify({"message": "Username aggiornato con successo", "user": {"id": user.id, "username": user.username}}), 200

@auth_bp.route("/update_password", methods=["PUT"])
@tag(["auth"])
@jwt_required
@validate_request(UpdatePasswordRequest)
async def update_password(data: UpdatePasswordRequest):
    """Modifica password"""
    user = await User.get_or_none(id=g.user["id"])
    
    if not user or not user.check_password(data.current_password):
        return jsonify({"error": "Credenziali attuali non valide"}), 401

    if len(data.new_password) < PASSWORD_MIN_LENGTH:
        return jsonify({"error": f"La nuova password deve essere di almeno {PASSWORD_MIN_LENGTH} caratteri"}), 400

    user.set_password(data.new_password)
    await user.save()
    return jsonify({"message": "Password aggiornata con successo"}), 200

