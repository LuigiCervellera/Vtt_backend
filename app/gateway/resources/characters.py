from quart import Blueprint, jsonify, g
from quart_schema import validate_request, tag
from models import Character, Campaign, User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.characters.schemas import CharacterCreate

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
    is_member = False
    if campaign.master_id == user_id:
        is_member = True
    else:
        is_member = await campaign.partecipanti.filter(id=user_id).exists()

    if not is_member:
        return jsonify({"error": "Non sei membro di questa campagna"}), 403

    proprietario = None
    if data.proprietario_id:
        proprietario = await User.get_or_none(id=data.proprietario_id)
        
    c = await Character.create(
        campagna=campaign,
        nome=data.nome,
        is_npc=data.is_npc,
        proprietario=proprietario,
        url_avatar=data.url_avatar,
        scheda_dati=data.scheda_dati or {}
    )
    return jsonify({"message": "Personaggio creato", "id": c.id}), 201

@characters_bp.route("/<int:character_id>", methods=["PUT", "DELETE"])
@tag(["characters"])
@jwt_required
async def manage_character(character_id: int):
    from quart import request
    user_id = g.user["id"]
    character = await Character.get_or_none(id=character_id).prefetch_related("campagna")
    if not character:
        return jsonify({"error": "Personaggio non trovato"}), 404
    is_master = character.campagna.master_id == user_id
    is_owner = character.proprietario_id == user_id

    if request.method == "DELETE":
        if not is_master and not is_owner:
            return jsonify({"error": "Non hai i permessi per eliminare questo personaggio"}), 403
        await character.delete()
        return jsonify({"message": "Personaggio eliminato"}), 200

    elif request.method == "PUT":
        if not is_master and not is_owner:
            return jsonify({"error": "Non hai i permessi per modificare questo personaggio"}), 403
        
        req_data = await request.get_json()
        if "nome" in req_data:
            character.nome = req_data["nome"]
        if "is_npc" in req_data:
            character.is_npc = req_data["is_npc"]
        if "url_avatar" in req_data:
            character.url_avatar = req_data["url_avatar"]
        if "scheda_dati" in req_data:
            character.scheda_dati = req_data["scheda_dati"]
            
        await character.save()
        return jsonify({"message": "Personaggio aggiornato", "id": character.id}), 200
