import re
import secrets
import logging
from quart import Blueprint, jsonify, request, g, make_response
from quart_schema import validate_request, tag
import jwt
import uuid
import datetime
from models import User, EmailVerificationToken
from app.app_modules.auth.decorators import jwt_required, rate_limit
from app.app_modules.auth.schemas import (
    AuthRequest, RegisterRequest, VerifyEmailRequest, ResendVerificationRequest,
    UpdateUsernameRequest, UpdatePasswordRequest
)
from app.app_modules.auth.blacklist import blacklist_token
from app.app_modules.auth.email import send_verification_email
from app.app_modules.base.config import (
    JWT_EXP_DELTA_SECONDS, JWT_SECRET, JWT_ALGORITHM,
    PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH, USERNAME_MIN_LENGTH, USERNAME_MAX_LENGTH,
    COOKIE_SECURE, COOKIE_SAMESITE,
)

logger = logging.getLogger("vtt.auth")

# Regex validation
_USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]+$")
_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

@auth_bp.route("/register", methods=["POST"])
@tag(["auth"])
@rate_limit(limit=5, period=60)
@validate_request(RegisterRequest)
async def register(data: RegisterRequest):
    """Registrazione nuovo utente con supporto email facoltativa/conferma"""
    username = data.username.strip()
    password = data.password
    email = data.email.strip().lower() if data.email else None

    if not username or not password:
        return jsonify({"error": "Username e password richiesti"}), 400

    # Validazione username
    if len(username) < USERNAME_MIN_LENGTH or len(username) > USERNAME_MAX_LENGTH:
        return jsonify({"error": f"L'username deve essere tra {USERNAME_MIN_LENGTH} e {USERNAME_MAX_LENGTH} caratteri"}), 400
    if not _USERNAME_REGEX.match(username):
        return jsonify({"error": "L'username può contenere solo lettere, numeri e underscore"}), 400

    # Validazione password
    if len(password) < PASSWORD_MIN_LENGTH or len(password) > PASSWORD_MAX_LENGTH:
        return jsonify({"error": f"La password deve essere tra {PASSWORD_MIN_LENGTH} e {PASSWORD_MAX_LENGTH} caratteri"}), 400

    # Validazione email se fornita
    if email:
        if not _EMAIL_REGEX.match(email):
            return jsonify({"error": "Formato email non valido"}), 400
        existing_email = await User.get_or_none(email=email)
        if existing_email:
            return jsonify({"error": "Indirizzo email già in uso"}), 409

    existing_user = await User.get_or_none(username=username)
    if existing_user:
        return jsonify({"error": "Username già in uso"}), 409

    user = User(username=username, email=email, is_email_verified=False if email else True)
    user.set_password(password)
    await user.save()

    # Se è presente un'email, generiamo il token di verifica e inviamo l'email
    token_str = None
    if email:
        token_str = secrets.token_urlsafe(32)
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
        await EmailVerificationToken.create(token=token_str, user=user, expires_at=expires_at)
        await send_verification_email(email, username, token_str)

    return jsonify({
        "message": "Utente registrato con successo. Controlla la tua email per verificare l'account." if email else "Utente registrato con successo",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_email_verified": user.is_email_verified
        }
    }), 201

@auth_bp.route("/verify-email", methods=["POST"])
@tag(["auth"])
@rate_limit(limit=10, period=60)
@validate_request(VerifyEmailRequest)
async def verify_email(data: VerifyEmailRequest):
    """Verifica il token inviato via email"""
    token_str = data.token.strip()
    if not token_str:
        return jsonify({"error": "Token mancante"}), 400

    token_obj = await EmailVerificationToken.get_or_none(token=token_str).prefetch_related("user")
    if not token_obj:
        return jsonify({"error": "Token di verifica non valido o scaduto"}), 400

    now = datetime.datetime.now(datetime.timezone.utc)
    if token_obj.expires_at < now:
        await token_obj.delete()
        return jsonify({"error": "Il link di verifica è scaduto. Richiedine uno nuovo."}), 400

    user = token_obj.user
    user.is_email_verified = True
    await user.save()
    
    # Rimuoviamo il token consumato
    await token_obj.delete()

    return jsonify({
        "message": "Email verificata con successo! Ora puoi accedere.",
        "user": {"id": user.id, "username": user.username, "email": user.email, "is_email_verified": True}
    }), 200

@auth_bp.route("/resend-verification", methods=["POST"])
@tag(["auth"])
@rate_limit(limit=3, period=60)
@validate_request(ResendVerificationRequest)
async def resend_verification(data: ResendVerificationRequest):
    """Reinvia un nuovo link di verifica email"""
    email = data.email.strip().lower()
    if not email or not _EMAIL_REGEX.match(email):
        return jsonify({"error": "Email non valida"}), 400

    user = await User.get_or_none(email=email)
    if not user:
        # Messaggio generico per evitare enumeration
        return jsonify({"message": "Se l'email è registrata, invieremo un nuovo link di verifica."}), 200

    if user.is_email_verified:
        return jsonify({"message": "Questa email è già stata verificata."}), 200

    # Cancella eventuali vecchi token
    await EmailVerificationToken.filter(user=user).delete()

    token_str = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    await EmailVerificationToken.create(token=token_str, user=user, expires_at=expires_at)
    await send_verification_email(email, user.username, token_str)

    return jsonify({"message": "Un nuovo link di verifica è stato inviato al tuo indirizzo email."}), 200

@auth_bp.route("/login", methods=["POST"])
@tag(["auth"])
@rate_limit(limit=10, period=60)
@validate_request(AuthRequest)
async def login(data: AuthRequest):
    """Login utente"""
    username = data.username
    password = data.password

    if not username or not password:
        return jsonify({"error": "Username e password richiesti"}), 400

    # Limit password length early to prevent CPU exhaustion on invalid/large inputs
    if len(password) > PASSWORD_MAX_LENGTH:
        return jsonify({"error": "Credenziali non valide"}), 401

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
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_email_verified": user.is_email_verified
            }
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
    return jsonify({
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_email_verified": user.is_email_verified
        }
    }), 200

@auth_bp.route("/logout", methods=["POST"])
@tag(["auth"])
@jwt_required
async def logout():
    """Logout utente (revoca token tramite blacklist JTI)"""
    jti = g.user.get("jti")
    if jti:
        await blacklist_token(jti)
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

    # Revoca il vecchio token (regola #11: JWT revocation on auth field change)
    old_jti = g.user.get("jti")
    if old_jti:
        await blacklist_token(old_jti)

    # Genera un nuovo token JWT per aggiornare l'identità dell'utente (username)
    payload = {
        "id": user.id,
        "username": user.username,
        "jti": str(uuid.uuid4()),
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=JWT_EXP_DELTA_SECONDS)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    resp_obj = await make_response(jsonify({
        "message": "Username aggiornato con successo",
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

@auth_bp.route("/update_password", methods=["PUT"])
@tag(["auth"])
@jwt_required
@validate_request(UpdatePasswordRequest)
async def update_password(data: UpdatePasswordRequest):
    """Modifica password"""
    user = await User.get_or_none(id=g.user["id"])
    
    if not user or not user.check_password(data.current_password):
        return jsonify({"error": "Credenziali attuali non valide"}), 401

    if len(data.new_password) < PASSWORD_MIN_LENGTH or len(data.new_password) > PASSWORD_MAX_LENGTH:
        return jsonify({"error": f"La nuova password deve essere tra {PASSWORD_MIN_LENGTH} e {PASSWORD_MAX_LENGTH} caratteri"}), 400

    user.set_password(data.new_password)
    await user.save()

    # Revoca il vecchio token e genera un nuovo JWT (regola #11: JWT revocation on auth field change)
    old_jti = g.user.get("jti")
    if old_jti:
        await blacklist_token(old_jti)

    payload = {
        "id": user.id,
        "username": user.username,
        "jti": str(uuid.uuid4()),
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=JWT_EXP_DELTA_SECONDS)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    resp_obj = await make_response(jsonify({"message": "Password aggiornata con successo"}))
    resp_obj.set_cookie(
        "vtt_token",
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=JWT_EXP_DELTA_SECONDS
    )
    return resp_obj, 200

