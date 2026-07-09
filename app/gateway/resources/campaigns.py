import uuid
import logging

from quart import Blueprint, jsonify, g
from quart_schema import validate_request, tag
from models import Campaign, User
from app.app_modules.auth.decorators import jwt_required
from app.app_modules.campaigns.schemas import CampaignCreate, CampaignJoin

logger = logging.getLogger(__name__)

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/api/campaigns")

@campaigns_bp.route("", methods=["GET"])
@tag(["campaigns"])
@jwt_required
async def get_campaigns():
    user_id = g.user["id"]
    mastered = await Campaign.filter(master_id=user_id).values("id", "nome_campagna", "codice_invito", "master_id", "system_id")
    
    user = await User.get(id=user_id).prefetch_related('campaigns_joined')
    joined = [{"id": c.id, "nome_campagna": c.nome_campagna, "codice_invito": c.codice_invito, "master_id": c.master_id, "system_id": c.system_id} for c in user.campaigns_joined]
    
    all_campaigns = list({c["id"]: c for c in mastered + joined}.values())
    return jsonify(all_campaigns), 200

@campaigns_bp.route("", methods=["POST"])
@tag(["campaigns"])
@jwt_required
@validate_request(CampaignCreate)
async def create_campaign(data: CampaignCreate):
    master = await User.get_or_none(id=g.user["id"])
    if not master:
        return jsonify({"error": "Master non trovato"}), 404
    logger.debug(f"create_campaign received system_id: {data.system_id}")
    try:
        # Genera codice invito sicuro lato server (8 caratteri hex)
        codice_invito = uuid.uuid4().hex[:8]
        campaign = await Campaign.create(
            nome_campagna=data.nome_campagna,
            codice_invito=codice_invito,
            system_id=data.system_id,
            master=master
        )
        return jsonify({"message": "Campagna creata", "id": campaign.id, "codice_invito": codice_invito, "system_id": campaign.system_id}), 201
    except Exception as e:
        logger.error(f"Errore durante la creazione della campagna: {e}", exc_info=True)
        return jsonify({"error": "Impossibile creare la campagna"}), 400

@campaigns_bp.route("/join", methods=["POST"])
@tag(["campaigns"])
@jwt_required
@validate_request(CampaignJoin)
async def join_campaign(data: CampaignJoin):
    user = await User.get_or_none(id=g.user["id"])
    if not user:
        return jsonify({"error": "Utente non trovato"}), 404
    
    campaign = await Campaign.get_or_none(codice_invito=data.codice_invito)
    if not campaign:
        return jsonify({"error": "Codice invito non valido"}), 404
    
    if campaign.master_id == user.id:
        return jsonify({"error": "Sei già il master di questa campagna"}), 400
    
    await campaign.partecipanti.add(user)
    return jsonify({"message": "Unito alla campagna con successo", "id": campaign.id}), 200

@campaigns_bp.route("/<int:campaign_id>", methods=["DELETE"])
@tag(["campaigns"])
@jwt_required
async def delete_campaign(campaign_id: int):
    user_id = g.user["id"]
    campaign = await Campaign.get_or_none(id=campaign_id)
    if not campaign:
        return jsonify({"error": "Campagna non trovata"}), 404
        
    if campaign.master_id != user_id:
        return jsonify({"error": "Non hai i permessi per eliminare questa campagna"}), 403
        
    await campaign.delete()
    return jsonify({"message": "Campagna eliminata"}), 200

@campaigns_bp.route("/<int:campaign_id>/leave", methods=["POST"])
@tag(["campaigns"])
@jwt_required
async def leave_campaign(campaign_id: int):
    user_id = g.user["id"]
    campaign = await Campaign.get_or_none(id=campaign_id)
    if not campaign:
        return jsonify({"error": "Campagna non trovata"}), 404
        
    if campaign.master_id == user_id:
        return jsonify({"error": "Il master non può abbandonare la campagna"}), 400
        
    user = await User.get_or_none(id=user_id)
    if not user:
        return jsonify({"error": "Utente non trovato"}), 404
    await campaign.partecipanti.remove(user)
    return jsonify({"message": "Hai abbandonato la campagna"}), 200
