import os
import uuid
from quart import Blueprint, jsonify, request, g, send_from_directory
from quart_schema import validate_request, tag
from werkzeug.utils import secure_filename
from models import Map, Campaign, User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.maps.schemas import MapCreate
from app.app_modules.base.config import UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_UPLOAD_SIZE

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
    return await send_from_directory(UPLOAD_FOLDER, filename)

@maps_bp.route('/api/upload-map', methods=['POST'])
@jwt_required
async def upload_map():
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
        # Leggi il contenuto per verificare la dimensione
        file_data = file.read()
        if len(file_data) > MAX_UPLOAD_SIZE:
            return jsonify({"error": "File troppo grande. Max 10MB"}), 413

        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        with open(file_path, "wb") as f:
            f.write(file_data)
        
        base_url = os.getenv("BASE_URL", "http://127.0.0.1:5000")
        url = f"{base_url}/uploads/{unique_filename}"
        return jsonify({"url": url}), 200
