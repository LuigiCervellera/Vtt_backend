import os
import uuid
import jwt
from quart import Blueprint, jsonify, request, g, send_from_directory, make_response
from quart_schema import validate_request, tag
from werkzeug.utils import secure_filename
from models import Map, Campaign, User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.maps.schemas import MapCreate
from app.app_modules.base.config import (
    UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_UPLOAD_SIZE,
    JWT_SECRET, JWT_ALGORITHM,
)
from app.app_modules.auth.blacklist import is_blacklisted
from app.app_modules.base.utils import is_safe_url

# Magic bytes per validazione contenuto file
_IMAGE_SIGNATURES = {
    b'\x89PNG\r\n\x1a\n': 'png',          # PNG
    b'\xff\xd8\xff': 'jpg',                 # JPEG
    b'RIFF': 'webp',                        # WebP (RIFF container)
}


def _validate_image_magic_bytes(file_data: bytes) -> str | None:
    """
    Verifica i magic bytes del file e ritorna l'estensione reale.
    Ritorna None se il file non è un'immagine valida.
    """
    for signature, ext in _IMAGE_SIGNATURES.items():
        if file_data[:len(signature)] == signature:
            # Per WebP, verifica anche che contenga "WEBP" all'offset 8
            if ext == 'webp':
                if len(file_data) >= 12 and file_data[8:12] == b'WEBP':
                    return ext
                continue
            return ext
    return None


_EXT_TO_MIMETYPE = {
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'webp': 'image/webp',
}

maps_bp = Blueprint("maps", __name__)

@maps_bp.route("/api/maps", methods=["GET"])
@tag(["maps"])
@jwt_required
async def get_maps():
    user_id = g.user["id"]
    # Restituisci solo le mappe delle campagne a cui l'utente partecipa
    user = await User.get(id=user_id).prefetch_related('campaigns_joined')
    mastered = await Campaign.filter(master_id=user_id).values_list("id", flat=True)
    joined_ids = [c.id for c in user.campaigns_joined]
    all_campaign_ids = list(set(mastered + joined_ids))
    maps = await Map.filter(campagna_id__in=all_campaign_ids).values()
    return jsonify(maps), 200

@maps_bp.route("/api/maps", methods=["POST"])
@tag(["maps"])
@jwt_required
@validate_request(MapCreate)
async def create_map(data: MapCreate):
    user_id = g.user["id"]
    campaign = await Campaign.get_or_none(id=data.campagna_id)
    if not campaign:
        return jsonify({"error": "Campagna non trovata"}), 404
    if campaign.master_id != user_id:
        return jsonify({"error": "Solo il master può creare mappe"}), 403
    if data.url_immagine and not is_safe_url(data.url_immagine):
        return jsonify({"error": "URL immagine non valido o non sicuro"}), 400
    m = await Map.create(
        campagna=campaign,
        nome_mappa=data.nome_mappa,
        url_immagine=data.url_immagine,
        is_active=data.is_active
    )
    return jsonify({"message": "Mappa creata", "id": m.id}), 201

@maps_bp.route("/api/maps/<int:map_id>", methods=["DELETE"])
@tag(["maps"])
@jwt_required
async def delete_map(map_id: int):
    user_id = g.user["id"]
    m = await Map.get_or_none(id=map_id).prefetch_related("campagna")
    if not m:
        return jsonify({"error": "Mappa non trovata"}), 404
    if m.campagna.master_id != user_id:
        return jsonify({"error": "Solo il master può eliminare mappe"}), 403
    await m.delete()
    return jsonify({"message": "Mappa eliminata"}), 200

@maps_bp.route('/uploads/<filename>', methods=['GET'])
async def uploaded_file(filename):
    """
    Serve i file caricati.
    I file sono protetti tramite URL non indovinabili (UUIDv4 a 128 bit generati all'upload),
    garantendo la sicurezza senza l'overhead di autenticazione CORS/Cookie su PixiJS.
    """

    # Determina il Content-Type corretto dall'estensione
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = _EXT_TO_MIMETYPE.get(ext, "application/octet-stream")

    response = await make_response(
        await send_from_directory(UPLOAD_FOLDER, filename)
    )
    response.headers["Content-Type"] = content_type
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

@maps_bp.route('/api/upload-map', methods=['POST'])
@jwt_required
async def upload_map():
    # Verifica Content-Length prima di leggere la richiesta
    content_length = request.content_length
    if content_length is not None and content_length > MAX_UPLOAD_SIZE:
        return jsonify({"error": "File troppo grande. Max 5MB"}), 413

    files = await request.files
    if 'mapImage' not in files:
        return jsonify({"error": "Nessuna immagine caricata"}), 400
    
    file = files['mapImage']
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
        with open(file_path, "wb") as f:
            f.write(file_data)
        
        file_url = f"/uploads/{unique_filename}"
        return jsonify({"url": file_url}), 200

