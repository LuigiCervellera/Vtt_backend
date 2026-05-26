from quart import Blueprint, jsonify, request, g
from quart_schema import validate_request, tag
import jwt
import datetime
from models import User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.auth.schemas import AuthRequest, UpdateUsernameRequest, UpdatePasswordRequest
from app.app_modules.base.config import JWT_EXP_DELTA_SECONDS, JWT_SECRET, JWT_ALGORITHM
from app.app_modules.base.redis_client import redis_manager

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@auth_bp.route("/register", methods=["POST"])
@tag(["auth"])
@validate_request(AuthRequest)
async def register(data: AuthRequest):
    """Registrazione nuovo utente"""
    username = data.username
    password = data.password

    if not username or not password:
        return jsonify({"error": "Username e password richiesti"}), 400

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
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=JWT_EXP_DELTA_SECONDS)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        if redis_manager.client:
            await redis_manager.client.set(f"token:{token}", user.id, ex=JWT_EXP_DELTA_SECONDS)
        
        return jsonify({
            "message": "Login effettuato", 
            "token": token,
            "user": {"id": user.id, "username": user.username}
        }), 200
    
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
    """Logout utente (revoca token)"""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        if redis_manager.client:
            await redis_manager.client.delete(f"token:{token}")
    return jsonify({"message": "Logout effettuato con successo"}), 200

@auth_bp.route("/update_username", methods=["PUT"])
@tag(["auth"])
@jwt_required
@validate_request(UpdateUsernameRequest)
async def update_username(data: UpdateUsernameRequest):
    """Modifica username"""
    user = await User.get_or_none(id=g.user["id"])
    
    if not user or not user.check_password(data.current_password):
        return jsonify({"error": "Credenziali attuali non valide"}), 401

    if data.new_username == user.username:
        return jsonify({"error": "Il nuovo username deve essere diverso dal precedente"}), 400

    existing = await User.get_or_none(username=data.new_username)
    if existing:
        return jsonify({"error": "Il nuovo username è già in uso"}), 409
        
    user.username = data.new_username
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

    user.set_password(data.new_password)
    await user.save()
    return jsonify({"message": "Password aggiornata con successo"}), 200
