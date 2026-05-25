import asyncio
import json
import jwt
import datetime
from functools import wraps
from quart import Quart, websocket, request, jsonify, g
from quart_cors import cors
from quart_schema import QuartSchema, validate_request, tag
from tortoise import Tortoise
from tortoise.contrib.quart import register_tortoise
from dataclasses import dataclass
from typing import Optional
from models import User, Campaign, Map, Character
import os
import uuid
import redis.asyncio as redis_async
from werkzeug.utils import secure_filename
from quart import send_from_directory

app = Quart(__name__)
app = cors(app, allow_origin="*", allow_headers="*", allow_methods="*")  # Configurazione CORS completa
QuartSchema(app, info={"title": "VTT API", "version": "1.0.0"})

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key")
JWT_ALGORITHM = "HS256"
JWT_EXP_DELTA_SECONDS = 86400 * 30 # 30 giorni

redis_client = None

def jwt_required(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token mancante o non valido"}), 401
        
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            # Check redis
            if redis_client:
                exists = await redis_client.exists(f"token:{token}")
                if not exists:
                    return jsonify({"error": "Token revocato o non valido"}), 401
            
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token scaduto"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token non valido"}), 401
            
        return await f(*args, **kwargs)
    return decorated


@dataclass
class AuthRequest:
    username: str
    password: str

@dataclass
class UpdateUsernameRequest:
    username: str
    current_password: str
    new_username: str

@dataclass
class UpdatePasswordRequest:
    username: str
    current_password: str
    new_password: str

@dataclass
class CampaignCreate:
    nome_campagna: str
    codice_invito: str

@dataclass
class CampaignJoin:
    codice_invito: str

@dataclass
class MapCreate:
    campagna_id: int
    nome_mappa: str
    url_immagine: str
    is_active: bool = False

@dataclass
class CharacterCreate:
    campagna_id: int
    nome: str
    is_npc: bool = False
    proprietario_id: Optional[int] = None
    url_avatar: Optional[str] = None
    scheda_dati: Optional[dict] = None

@app.before_serving
async def init_orm():
    global redis_client
    redis_client = await redis_async.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    await Tortoise.init(
        db_url=os.getenv("DATABASE_URL", "postgres://postgres:admin@localhost:5432/vtt_db"),
        modules={"models": ["models"]},
        _enable_global_fallback=True
    )
    await Tortoise.generate_schemas()

@app.after_serving
async def close_orm():
    global redis_client
    if redis_client:
        await redis_client.aclose()
    await Tortoise.close_connections()

# Questa struttura dati terrà traccia dei giocatori connessi nelle varie stanze.
# Struttura: { "id_stanza": { websocket1: "username", ... } }
connected_rooms = {}
room_chat_history = {} # { "id_stanza": [msg1, msg2, ...] }
room_grid_settings = {} # { "id_stanza": {"snapToGrid": False} }
room_current_map = {} # { "id_stanza": "url" }
room_tokens = {} # { "id_stanza": { "token_id": {"x": 200, "y": 200, "color": 0xa855f7} } }

@app.route("/")
async def index():
    return {"status": "VTT Backend is running!"}

@app.route('/uploads/<filename>')
async def uploaded_file(filename):
    return await send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/upload-map', methods=['POST'])
async def upload_map():
    files = await request.files
    if 'mapImage' not in files:
        return jsonify({"error": "Nessuna immagine caricata"}), 400
    
    file = files['mapImage']
    if file.filename == '':
        return jsonify({"error": "Nome file vuoto"}), 400
        
    if file:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        await file.save(file_path)
        
        # We return the absolute URL for MVP
        url = f"http://127.0.0.1:5000/uploads/{unique_filename}"
        return jsonify({"url": url}), 200

@app.route("/api/auth/register", methods=["POST"])
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

@app.route("/api/auth/login", methods=["POST"])
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
        
        if redis_client:
            await redis_client.set(f"token:{token}", user.id, ex=JWT_EXP_DELTA_SECONDS)
        
        return jsonify({
            "message": "Login effettuato", 
            "token": token,
            "user": {"id": user.id, "username": user.username}
        }), 200
    
    return jsonify({"error": "Credenziali non valide"}), 401

@app.route("/api/auth/me", methods=["GET"])
@tag(["auth"])
@jwt_required
async def get_me():
    """Restituisce l'utente corrente in base al JWT"""
    user = await User.get_or_none(id=g.user["id"])
    if not user:
        return jsonify({"error": "Utente non trovato"}), 404
    return jsonify({"user": {"id": user.id, "username": user.username}}), 200

@app.route("/api/auth/logout", methods=["POST"])
@tag(["auth"])
@jwt_required
async def logout():
    """Logout utente (revoca token)"""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        if redis_client:
            await redis_client.delete(f"token:{token}")
    return jsonify({"message": "Logout effettuato con successo"}), 200

@app.route("/api/auth/update_username", methods=["PUT"])
@tag(["auth"])
@validate_request(UpdateUsernameRequest)
async def update_username(data: UpdateUsernameRequest):
    """Modifica username"""
    user = await User.get_or_none(username=data.username)
    
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

@app.route("/api/auth/update_password", methods=["PUT"])
@tag(["auth"])
@validate_request(UpdatePasswordRequest)
async def update_password(data: UpdatePasswordRequest):
    """Modifica password"""
    user = await User.get_or_none(username=data.username)
    
    if not user or not user.check_password(data.current_password):
        return jsonify({"error": "Credenziali attuali non valide"}), 401

    user.set_password(data.new_password)
    await user.save()
    return jsonify({"message": "Password aggiornata con successo"}), 200

# --- CRUD CAMPAIGNS ---
@app.route("/api/campaigns", methods=["GET"])
@tag(["campaigns"])
@jwt_required
async def get_campaigns():
    user_id = g.user["id"]
    mastered = await Campaign.filter(master_id=user_id).values("id", "nome_campagna", "codice_invito", "master_id")
    
    user = await User.get(id=user_id).prefetch_related('campaigns_joined')
    joined = [{"id": c.id, "nome_campagna": c.nome_campagna, "codice_invito": c.codice_invito, "master_id": c.master_id} for c in user.campaigns_joined]
    
    all_campaigns = list({c["id"]: c for c in mastered + joined}.values())
    return jsonify(all_campaigns), 200

@app.route("/api/campaigns", methods=["POST"])
@tag(["campaigns"])
@jwt_required
@validate_request(CampaignCreate)
async def create_campaign(data: CampaignCreate):
    master = await User.get_or_none(id=g.user["id"])
    if not master:
        return jsonify({"error": "Master non trovato"}), 404
    try:
        campaign = await Campaign.create(
            nome_campagna=data.nome_campagna,
            codice_invito=data.codice_invito,
            master=master
        )
        return jsonify({"message": "Campagna creata", "id": campaign.id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/campaigns/join", methods=["POST"])
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

@app.route("/api/campaigns/<int:campaign_id>", methods=["DELETE"])
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

@app.route("/api/campaigns/<int:campaign_id>/leave", methods=["POST"])
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
    await campaign.partecipanti.remove(user)
    return jsonify({"message": "Hai abbandonato la campagna"}), 200

# --- CRUD MAPS ---
@app.route("/api/maps", methods=["GET"])
@tag(["maps"])
async def get_maps():
    maps = await Map.all().values()
    return jsonify(maps), 200

@app.route("/api/maps", methods=["POST"])
@tag(["maps"])
@validate_request(MapCreate)
async def create_map(data: MapCreate):
    campaign = await Campaign.get_or_none(id=data.campagna_id)
    if not campaign:
        return jsonify({"error": "Campagna non trovata"}), 404
    m = await Map.create(
        campagna=campaign,
        nome_mappa=data.nome_mappa,
        url_immagine=data.url_immagine,
        is_active=data.is_active
    )
    return jsonify({"message": "Mappa creata", "id": m.id}), 201

@app.route("/api/maps/<int:map_id>", methods=["DELETE"])
@tag(["maps"])
async def delete_map(map_id: int):
    deleted = await Map.filter(id=map_id).delete()
    if deleted:
        return jsonify({"message": "Mappa eliminata"}), 200
    return jsonify({"error": "Mappa non trovata"}), 404

# --- CRUD CHARACTERS ---
@app.route("/api/characters", methods=["GET"])
@tag(["characters"])
async def get_characters():
    characters = await Character.all().values()
    return jsonify(characters), 200

@app.route("/api/characters", methods=["POST"])
@tag(["characters"])
@validate_request(CharacterCreate)
async def create_character(data: CharacterCreate):
    campaign = await Campaign.get_or_none(id=data.campagna_id)
    if not campaign:
        return jsonify({"error": "Campagna non trovata"}), 404
    
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

@app.route("/api/characters/<int:character_id>", methods=["DELETE"])
@tag(["characters"])
async def delete_character(character_id: int):
    deleted = await Character.filter(id=character_id).delete()
    if deleted:
        return jsonify({"message": "Personaggio eliminato"}), 200
    return jsonify({"error": "Personaggio non trovato"}), 404

@app.websocket("/ws")
async def ws_endpoint():
    """
    Questo endpoint gestisce le connessioni WebSocket in tempo reale.
    Ogni volta che un giocatore si connette, Quart mantiene questa funzione attiva.
    """
    # Accetta la connessione in entrata dal browser del giocatore
    await websocket.accept()
    
    current_room = None
    ws_obj = websocket._get_current_object()
    
    try:
        while True:
            # Rimane in ascolto di nuovi messaggi dal client
            raw_data = await websocket.receive()
            data = json.loads(raw_data)
            
            msg_type = data.get("type")
            payload = data.get("payload", {})
            room_id = payload.get("roomId")
            
            # 1. GESTIONE INGRESSO STANZA
            if msg_type == "JOIN_ROOM" and room_id:
                current_room = room_id
                username = payload.get("username", "Anonimo")
                user_id = payload.get("userId")
                
                is_master = False
                if user_id:
                    try:
                        campaign = await Campaign.get_or_none(id=int(room_id))
                        if campaign and campaign.master_id == user_id:
                            is_master = True
                    except Exception:
                        pass
                
                if current_room not in connected_rooms:
                    connected_rooms[current_room] = {}
                
                # Aggiunge il WebSocket corrente alla lista della stanza
                connected_rooms[current_room][ws_obj] = {
                    "username": username,
                    "user_id": user_id,
                    "is_master": is_master
                }
                print(f"-> Utente {username} entrato nella stanza: {current_room}")
                
                if current_room not in room_chat_history:
                    room_chat_history[current_room] = []
                    
                if current_room not in room_tokens:
                    # Inizializza un token di default per la stanza (MVP)
                    room_tokens[current_room] = {
                        "test-token": {"x": 200, "y": 200, "color": 0xa855f7}
                    }
                    
                # Invia lo storico della chat al nuovo utente
                history_message = json.dumps({
                    "type": "CHAT_HISTORY",
                    "payload": {"messages": room_chat_history[current_room]}
                })
                await ws_obj.send(history_message)
                
                # Invia i token correnti
                tokens_message = json.dumps({
                    "type": "SYNC_TOKENS",
                    "payload": {"tokens": room_tokens[current_room]}
                })
                await ws_obj.send(tokens_message)
                
                # Invia le impostazioni della griglia se presenti
                if current_room in room_grid_settings:
                    grid_message = json.dumps({
                        "type": "GRID_SETTINGS",
                        "payload": room_grid_settings[current_room]
                    })
                    await ws_obj.send(grid_message)

                # Invia la mappa corrente se presente
                if current_room in room_current_map:
                    map_message = json.dumps({
                        "type": "SET_MAP",
                        "payload": {"url": room_current_map[current_room]}
                    })
                    await ws_obj.send(map_message)
                
                # Broadcast della lista giocatori aggiornata
                players = [info["username"] if isinstance(info, dict) else info for info in connected_rooms[current_room].values()]
                broadcast_message = json.dumps({
                    "type": "UPDATE_PLAYERS",
                    "payload": {"players": players}
                })
                for client in list(connected_rooms[current_room].keys()):
                    await client.send(broadcast_message)
            
            # 2. GESTIONE MOVIMENTO TOKEN (BROADCAST)
            elif msg_type == "MOVE_TOKEN" and current_room:
                token_id = payload.get("tokenId")
                
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
                
                is_authorized = False
                if token_id:
                    if current_room not in room_tokens:
                        room_tokens[current_room] = {}
                    
                    if token_id not in room_tokens[current_room]:
                        # Creazione nuovo token: permesso a tutti
                        is_authorized = True
                    else:
                        # Spostamento: permesso al creatore o al master
                        owner_id = room_tokens[current_room][token_id].get("owner_id")
                        is_owner = (str(owner_id) == str(user_id)) if owner_id and user_id else False
                        if is_master or is_owner:
                            is_authorized = True

                if is_authorized:
                    if token_id not in room_tokens[current_room]:
                        room_tokens[current_room][token_id] = {
                            "color": payload.get("color", 0xa855f7),
                            "owner_id": payload.get("ownerId", payload.get("owner_id"))
                        }
                    room_tokens[current_room][token_id]["x"] = payload.get("x", 0)
                    room_tokens[current_room][token_id]["y"] = payload.get("y", 0)

                    targets = connected_rooms.get(current_room, {}).keys()
                    broadcast_message = json.dumps({
                        "type": "MOVE_TOKEN",
                        "payload": payload
                    })
                    
                    for client in targets:
                        if client != ws_obj:
                            await client.send(broadcast_message)
                        
            # 2.5 REMOVE TOKEN
            elif msg_type == "REMOVE_TOKEN" and current_room:
                token_id = payload.get("tokenId")
                
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
                
                if token_id and current_room in room_tokens:
                    token_data = room_tokens[current_room].get(token_id)
                    if token_data:
                        owner_id = token_data.get("owner_id")
                        is_owner = (str(owner_id) == str(user_id)) if owner_id and user_id else False
                        
                        if is_master or is_owner:
                            del room_tokens[current_room][token_id]
                            
                            targets = connected_rooms.get(current_room, {}).keys()
                            broadcast_message = json.dumps({
                                "type": "REMOVE_TOKEN",
                                "payload": {"tokenId": token_id}
                            })
                            for client in targets:
                                await client.send(broadcast_message)
                        
            # 3. CHAT MESSAGE (BROADCAST)
            elif msg_type == "CHAT_MESSAGE" and current_room:
                if current_room not in room_chat_history:
                    room_chat_history[current_room] = []
                room_chat_history[current_room].append(payload)
                if len(room_chat_history[current_room]) > 100:
                    room_chat_history[current_room] = room_chat_history[current_room][-100:]
                    
                targets = connected_rooms.get(current_room, {}).keys()
                broadcast_message = json.dumps({
                    "type": "CHAT_MESSAGE",
                    "payload": payload
                })
                
                # Invia a tutti, compreso il mittente, così si assicura che sia stato ricevuto
                for client in targets:
                    await client.send(broadcast_message)
                    
            # 4. GRID SETTINGS (BROADCAST)
            elif msg_type == "GRID_SETTINGS" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_grid_settings[current_room] = payload
                    targets = connected_rooms.get(current_room, {}).keys()
                    broadcast_message = json.dumps({
                        "type": "GRID_SETTINGS",
                        "payload": payload
                    })
                    for client in targets:
                        await client.send(broadcast_message)

            # 5. SET MAP (BROADCAST)
            elif msg_type == "SET_MAP" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_current_map[current_room] = payload.get("url")
                    targets = connected_rooms.get(current_room, {}).keys()
                    broadcast_message = json.dumps({
                        "type": "SET_MAP",
                        "payload": payload
                    })
                    for client in targets:
                        await client.send(broadcast_message)

            # 6. CLEAR MAP (BROADCAST)
            elif msg_type == "CLEAR_MAP" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_tokens[current_room] = {}
                    targets = connected_rooms.get(current_room, {}).keys()
                    broadcast_message = json.dumps({
                        "type": "CLEAR_MAP",
                        "payload": {}
                    })
                    for client in targets:
                        await client.send(broadcast_message)

    except asyncio.CancelledError:
        # Gestisce la disconnessione pulita del browser (es. chiusura scheda)
        pass
    finally:
        # Se il giocatore si disconnette, lo rimuoviamo dalla stanza
        if current_room and current_room in connected_rooms:
            connected_rooms[current_room].pop(ws_obj, None)
            print(f"<- Un utente ha lasciato la stanza: {current_room}")
            
            players = [info["username"] if isinstance(info, dict) else info for info in connected_rooms[current_room].values()]
            broadcast_message = json.dumps({
                "type": "UPDATE_PLAYERS",
                "payload": {"players": players}
            })
            for client in list(connected_rooms[current_room].keys()):
                await client.send(broadcast_message)
                
            if not connected_rooms[current_room]:
                del connected_rooms[current_room]

if __name__ == "__main__":
    # Avvia il server in modalità sviluppo sulla porta 5000
    app.run(host="127.0.0.1", port=5000, debug=True)