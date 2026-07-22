from quart import Blueprint, jsonify, g
from quart_schema import validate_request, tag
from models import Character, Campaign, User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.characters.schemas import CharacterCreate, CharacterUpdate
from app.app_modules.characters.validation import sanitize_scheda_dati, SchedaValidationError
from app.app_modules.base.utils import is_safe_url
import os
import uuid
from werkzeug.utils import secure_filename
from quart import request
from app.app_modules.base.config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_UPLOAD_SIZE
from app.gateway.resources.maps import _validate_image_magic_bytes

characters_bp = Blueprint("characters", __name__, url_prefix="/api/characters")

@characters_bp.route("", methods=["GET"])
@tag(["characters"])
@jwt_required
async def get_characters():
    user_id = g.user["id"]
    user = await User.get(id=user_id).prefetch_related('campaigns_joined')
    mastered = await Campaign.filter(master_id=user_id).values_list("id", flat=True)
    joined_ids = [c.id for c in user.campaigns_joined]
    all_campaign_ids = list(set(mastered + joined_ids))
    characters = await Character.filter(campagna_id__in=all_campaign_ids).values()
    return jsonify(characters), 200

@characters_bp.route("", methods=["POST"])
@tag(["characters"])
@jwt_required
@validate_request(CharacterCreate)
async def create_character(data: CharacterCreate):
    user_id = g.user["id"]
    campaign = await Campaign.get_or_none(id=data.campagna_id)
    if not campaign:
        return jsonify({"error": "Campagna non trovata"}), 404

    # Verifica che l'utente sia membro della campagna (master o partecipante)
    is_master = campaign.master_id == user_id
    is_member = False
    if is_master:
        is_member = True
    else:
        is_member = await campaign.partecipanti.filter(id=user_id).exists()

    if not is_member:
        return jsonify({"error": "Non sei membro di questa campagna"}), 403

    if data.url_avatar and not is_safe_url(data.url_avatar):
        return jsonify({"error": "URL avatar non valido o non sicuro"}), 400

    proprietario = None
    if is_master:
        if data.proprietario_id:
            # Verifica che il proprietario sia membro della campagna o il master stesso
            is_owner_member = (data.proprietario_id == campaign.master_id) or \
                              await campaign.partecipanti.filter(id=data.proprietario_id).exists()
            if not is_owner_member:
                return jsonify({"error": "Il proprietario specificato non è membro di questa campagna"}), 400
            proprietario = await User.get_or_none(id=data.proprietario_id)
        is_npc = data.is_npc
    else:
        # I giocatori normali possono creare solo personaggi legati a se stessi e non NPC
        proprietario = await User.get_or_none(id=user_id)
        is_npc = False

    try:
        scheda_dati_sanitized = sanitize_scheda_dati(data.scheda_dati or {})
    except SchedaValidationError as e:
        return jsonify({"error": str(e)}), 400

    c = await Character.create(
        campagna=campaign,
        nome=data.nome,
        is_npc=is_npc,
        proprietario=proprietario,
        url_avatar=data.url_avatar,
        scheda_dati=scheda_dati_sanitized
    )
    return jsonify({"message": "Personaggio creato", "id": c.id}), 201

@characters_bp.route("/<int:character_id>", methods=["PUT"])
@tag(["characters"])
@jwt_required
@validate_request(CharacterUpdate)
async def update_character(character_id: int, data: CharacterUpdate):
    user_id = g.user["id"]
    character = await Character.get_or_none(id=character_id).prefetch_related("campagna")
    if not character:
        return jsonify({"error": "Personaggio non trovato"}), 404
        
    is_master = character.campagna.master_id == user_id
    is_owner = character.proprietario_id == user_id

    if not is_master and not is_owner:
        return jsonify({"error": "Non hai i permessi per modificare questo personaggio"}), 403

    if data.url_avatar and not is_safe_url(data.url_avatar):
        return jsonify({"error": "URL avatar non valido o non sicuro"}), 400

    if data.nome is not None:
        character.nome = data.nome
    if data.is_npc is not None:
        if not is_master:
            return jsonify({"error": "Solo il master può cambiare lo stato NPC"}), 403
        character.is_npc = data.is_npc
    if data.url_avatar is not None:
        character.url_avatar = data.url_avatar
    if data.scheda_dati is not None:
        try:
            character.scheda_dati = sanitize_scheda_dati(data.scheda_dati)
        except SchedaValidationError as e:
            return jsonify({"error": str(e)}), 400

    await character.save()
    return jsonify({"message": "Personaggio aggiornato", "id": character.id}), 200


@characters_bp.route("/<int:character_id>", methods=["DELETE"])
@tag(["characters"])
@jwt_required
async def delete_character(character_id: int):
    user_id = g.user["id"]
    character = await Character.get_or_none(id=character_id).prefetch_related("campagna")
    if not character:
        return jsonify({"error": "Personaggio non trovato"}), 404
        
    is_master = character.campagna.master_id == user_id
    is_owner = character.proprietario_id == user_id

    if not is_master and not is_owner:
        return jsonify({"error": "Non hai i permessi per eliminare questo personaggio"}), 403

    await character.delete()
    return jsonify({"message": "Personaggio eliminato"}), 200

@characters_bp.route("/upload-avatar", methods=["POST"])
@tag(["characters"])
@jwt_required
async def upload_avatar():
    # Verifica Content-Length prima di leggere la richiesta
    content_length = request.content_length
    if content_length is not None and content_length > MAX_UPLOAD_SIZE:
        return jsonify({"error": "File troppo grande. Max 5MB"}), 413

    files = await request.files
    if 'avatar' not in files:
        return jsonify({"error": "Nessuna immagine caricata"}), 400
    
    file = files['avatar']
    if file.filename == '':
        return jsonify({"error": "Nome file vuoto"}), 400
    
    # Validazione estensione file
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Tipo file non permesso. Ammessi: {', '.join(ALLOWED_EXTENSIONS)}"}), 400
        
    if file:
        # Leggi solo fino al limite massimo consentito + 1 per prevenire l'esaurimento della memoria
        file_data = file.read(MAX_UPLOAD_SIZE + 1)
        if len(file_data) > MAX_UPLOAD_SIZE:
            return jsonify({"error": "File troppo grande. Max 5MB"}), 413

        # Validazione magic bytes — verifica che il contenuto sia un'immagine reale
        detected_ext = _validate_image_magic_bytes(file_data)
        if detected_ext is None:
            return jsonify({"error": "Il file non è un'immagine valida. Il contenuto non corrisponde a PNG, JPEG o WebP"}), 400

        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        
        # Salva il file su disco
        with open(file_path, "wb") as f:
            f.write(file_data)
            
        # Ritorna l'URL relativo per accedere al file tramite l'endpoint /uploads/<filename> esistente
        file_url = f"/uploads/{unique_filename}"
        return jsonify({"message": "Avatar caricato con successo", "url": file_url}), 201
