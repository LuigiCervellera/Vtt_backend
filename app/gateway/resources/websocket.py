import json
import asyncio
import jwt
import logging
import re
import random
import time

logger = logging.getLogger(__name__)
from quart import Blueprint, websocket
from models import Campaign, User
from app.app_modules.base.config import JWT_SECRET, JWT_ALGORITHM, MAX_WS_MESSAGE_SIZE
from app.app_modules.auth.blacklist import is_blacklisted
from app.app_modules.base.utils import is_safe_url


from typing import TypedDict, List, Optional

class RollResult(TypedDict):
    results: List[int]
    total: int
    modifier: int
    faces: int
    keep: Optional[str]


def parse_and_roll_single(formula: str) -> RollResult:
    clean = formula.replace(" ", "")
    # Check if constant
    if re.match(r"^[+-]?\d+$", clean):
        val = int(clean)
        return {"results": [], "total": val, "modifier": val, "faces": 0, "keep": None}
    
    # Check if dice term (supports kh1 and kl1 for advantage/disadvantage)
    match = re.match(r"^([+-]?\d*)?[dD](\d+)(kh1|kl1)?([+-]\d+)?$", clean, re.IGNORECASE)
    if match:
        sign_str = match.group(1)
        sign = -1 if (sign_str and sign_str.startswith("-")) else 1
        
        count_str = sign_str.replace("+", "").replace("-", "") if sign_str else "1"
        count = int(count_str) if count_str else 1
        count = max(1, min(count, 100)) # Sane limits to prevent DoS
        
        faces = int(match.group(2))
        faces = max(1, min(faces, 1000)) # Sane limits to prevent DoS
        
        keep_mode = match.group(3).lower() if match.group(3) else None
        modifier = int(match.group(4)) if match.group(4) else 0
        
        results = [random.randint(1, faces) for _ in range(count)]
        if keep_mode == "kh1":
            total = (max(results) if results else 0) * sign + modifier
        elif keep_mode == "kl1":
            total = (min(results) if results else 0) * sign + modifier
        else:
            total = sum(results) * sign + modifier

        return {"results": results, "total": total, "modifier": modifier, "faces": faces, "keep": keep_mode}
    
    return {"results": [], "total": 0, "modifier": 0, "faces": 0, "keep": None}



def roll_expression(expression: str, sources=None):
    if sources and isinstance(sources, list) and len(sources) > 0:
        rolled_sources = []
        total = 0
        results = []
        modifier = 0
        faces = 20
        
        for src in sources[:10]:
            if not isinstance(src, dict):
                continue
            formula = str(src.get("formula", ""))[:50]
            rolled = parse_and_roll_single(formula)
            
            rolled_sources.append({
                "name": str(src.get("name", ""))[:100],
                "formula": formula,
                "results": rolled["results"],
                "total": rolled["total"],
                "modifier": rolled["modifier"],
                "type": str(src.get("type", "base"))[:20]
            })
            total += rolled["total"]
            results.extend(rolled["results"])
            
            if len(rolled["results"]) == 0:
                modifier += rolled["modifier"]
                
            if src.get("type") == "base" and rolled["faces"]:
                faces = rolled["faces"]
                
        return {
            "results": results,
            "total": total,
            "modifier": modifier,
            "faces": faces,
            "sources": rolled_sources
        }
    else:
        # Split expression into terms
        terms = re.findall(r"([+-]?[^+-]+)", expression.replace(" ", ""))
        if not terms:
            terms = [expression]
            
        rolled_sources = []
        total = 0
        results = []
        modifier = 0
        faces = 20
        
        for idx, term in enumerate(terms[:10]):
            rolled = parse_and_roll_single(term)
            rolled_sources.append({
                "name": "Base" if idx == 0 else f"Modificatore {idx}",
                "formula": term,
                "results": rolled["results"],
                "total": rolled["total"],
                "modifier": rolled["modifier"],
                "type": 'base' if idx == 0 else 'effect'
            })
            total += rolled["total"]
            results.extend(rolled["results"])
            if len(rolled["results"]) == 0:
                modifier += rolled["modifier"]
                
            if idx == 0 and rolled["faces"]:
                faces = rolled["faces"]
                
        return {
            "results": results,
            "total": total,
            "modifier": modifier,
            "faces": faces,
            "sources": rolled_sources
        }


ws_bp = Blueprint("ws", __name__)

# State dictionaries for real-time rooms
connected_rooms = {}
room_chat_history = {}
room_grid_settings = {}
room_current_map = {}
room_tokens = {}
room_templates = {}
room_walls = {}
room_fow_enabled = {}
room_los_enabled = {}
room_initiative = {}
room_scenes = {}
room_active_scene_id = {}

# Throttling e salvataggio periodico dello stato per evitare DDoS / rallentamenti
dirty_campaigns = set()
background_loop_task = None

async def _save_campaign_state_immediate(room_id: str):
    """
    Salva immediatamente lo stato in memoria di una stanza nel database.
    """
    try:
        campaign_id = int(room_id)
        campaign = await Campaign.get_or_none(id=campaign_id)
        if not campaign:
            return
            
        updated = False
        if room_current_map.get(room_id) is not None:
            campaign.current_map_url = room_current_map[room_id]
            updated = True
        if room_grid_settings.get(room_id) is not None:
            campaign.grid_settings = room_grid_settings[room_id]
            updated = True
        if room_tokens.get(room_id) is not None:
            campaign.tokens = room_tokens[room_id]
            updated = True
        if room_templates.get(room_id) is not None:
            campaign.templates = room_templates[room_id]
            updated = True
        if room_walls.get(room_id) is not None:
            campaign.walls = room_walls[room_id]
            updated = True
        if room_fow_enabled.get(room_id) is not None:
            campaign.fow_enabled = room_fow_enabled[room_id]
            updated = True
        if room_los_enabled.get(room_id) is not None:
            campaign.los_enabled = room_los_enabled[room_id]
            updated = True
        if room_initiative.get(room_id) is not None:
            campaign.initiative = room_initiative[room_id]
            updated = True
        if room_chat_history.get(room_id) is not None:
            campaign.chat_history = room_chat_history[room_id]
            updated = True
        if room_scenes.get(room_id) is not None:
            campaign.scenes = room_scenes[room_id]
            updated = True
        if room_active_scene_id.get(room_id) is not None:
            campaign.active_scene_id = room_active_scene_id[room_id]
            updated = True
            
        if updated:
            await campaign.save()
            logger.info(f"Stato della stanza {room_id} persistito con successo nel database.")
    except Exception as e:
        logger.error(f"Errore durante il salvataggio immediato per la stanza {room_id}: {e}", exc_info=True)

async def _save_dirty_campaigns_loop():
    """
    Ciclo in background che salva ogni 5 secondi tutte le campagne modificate.
    """
    while True:
        try:
            await asyncio.sleep(5.0)
            if not dirty_campaigns:
                continue
                
            to_save = list(dirty_campaigns)
            dirty_campaigns.clear()
            
            for room_id in to_save:
                await _save_campaign_state_immediate(room_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Errore nel loop di salvataggio dello stato tabletop: {e}", exc_info=True)


async def _broadcast(room_id: str, message: str, exclude=None):
    """
    Invia un messaggio a tutti i client nella stanza in modo concorrente.
    Previene l'Head-of-Line blocking se un client ha una connessione lenta.
    Rimuove automaticamente i client disconnessi o in timeout.
    """
    if room_id not in connected_rooms:
        return
    
    clients = [c for c in list(connected_rooms[room_id].keys()) if c != exclude]
    if not clients:
        return

    # Invia in parallelo con un timeout di 2.0 secondi per client
    tasks = [asyncio.wait_for(c.send(message), timeout=2.0) for c in clients]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    stale_clients = []
    for client, result in zip(clients, results):
        if isinstance(result, Exception):
            stale_clients.append(client)
            
    # Rimozione sicura dei client disconnessi o eccessivamente lenti
    for client in stale_clients:
        connected_rooms[room_id].pop(client, None)


async def _broadcast_initiative(room_id: str):
    """
    Spedisce lo stato del tracker iniziativa a tutti i client connessi.
    I master ricevono lo stato completo (inclusi i nemici nascosti).
    I giocatori ricevono solo i token visibili, mappando l'indice del turno attivo.
    """
    if room_id not in connected_rooms or room_id not in room_initiative:
        return
    
    init_state = room_initiative[room_id]
    master_msg = json.dumps({
        "type": "UPDATE_INITIATIVE",
        "payload": init_state
    })
    
    clients = list(connected_rooms[room_id].keys())
    tasks = []
    
    for client in clients:
        client_info = connected_rooms[room_id].get(client)
        if not client_info:
            continue
            
        if client_info.get("is_master"):
            tasks.append(asyncio.wait_for(client.send(master_msg), timeout=2.0))
        else:
            full_order = init_state.get("order", [])
            filtered_order = []
            player_active_idx = None
            
            for idx, item in enumerate(full_order):
                if item.get("is_visible", True):
                    filtered_order.append(item)
                    if idx == init_state.get("active_idx"):
                        player_active_idx = len(filtered_order) - 1
                else:
                    if idx == init_state.get("active_idx"):
                        player_active_idx = None
            
            player_state = {
                "active": init_state.get("active", False),
                "round": init_state.get("round", 1),
                "active_idx": player_active_idx,
                "order": filtered_order
            }
            
            player_msg = json.dumps({
                "type": "UPDATE_INITIATIVE",
                "payload": player_state
            })
            tasks.append(asyncio.wait_for(client.send(player_msg), timeout=2.0))
            
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        stale_clients = []
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                stale_clients.append(client)
        for client in stale_clients:
            connected_rooms[room_id].pop(client, None)


async def _check_campaign_membership(user_id: int, campaign_id: int) -> tuple[bool, bool]:
    """
    Verifica se l'utente è membro della campagna.
    Ritorna (is_member, is_master).
    """
    try:
        campaign = await Campaign.get_or_none(id=campaign_id)
        if not campaign:
            return False, False
        
        is_master = campaign.master_id == user_id
        if is_master:
            return True, True
        
        # Usa .filter().exists() invece di .all() — molto più veloce e sicuro
        is_participant = await campaign.partecipanti.filter(id=user_id).exists()
        return is_participant, False
    except Exception:
        return False, False


@ws_bp.websocket("/ws")
async def ws_endpoint():
    """
    Questo endpoint gestisce le connessioni WebSocket in tempo reale.
    Richiede autenticazione JWT tramite query parameter ?token=xxx
    """
    # Accetta l'handshake iniziale senza token esposto in query parameter
    await websocket.accept()
    
    # Attende il messaggio di autenticazione speciale entro 5.0 secondi
    try:
        raw_auth_data = await asyncio.wait_for(websocket.receive(), timeout=5.0)
        auth_data = json.loads(raw_auth_data)
        if auth_data.get("type") != "AUTH":
            raise ValueError("Il primo messaggio deve essere di tipo AUTH")
        
        # Sicurezza: l'autenticazione principale è basata su Cookie HttpOnly ("vtt_token")
        # trasmessi automaticamente durante l'handshake di upgrade HTTP del WebSocket.
        token = websocket.cookies.get("vtt_token")
        if not token:
            # Fallback solo per compatibilità all'interno di test automatizzati o client REST senza cookie
            token = auth_data.get("payload", {}).get("token")
            
        if not token:
            raise ValueError("Token di sessione mancante sia nei cookie che nel payload")
    except asyncio.TimeoutError:
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Timeout di autenticazione"}}))
        await websocket.close(4003)
        return
    except Exception as e:
        logger.error("WebSocket authentication error: %s", e, exc_info=True)
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Autenticazione fallita"}}))
        await websocket.close(4003)
        return
        
    # Verifica il token JWT
    try:
        jwt_payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token scaduto"}}))
        await websocket.close(4003)
        return
    except jwt.InvalidTokenError:
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token non valido"}}))
        await websocket.close(4003)
        return

    # Verifica blacklist del token
    jti = jwt_payload.get("jti")
    if jti and await is_blacklisted(jti):
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token revocato"}}))
        await websocket.close(4003)
        return

    # Identità verificata dal server
    authenticated_user_id = jwt_payload["id"]
    authenticated_username = jwt_payload["username"]
    
    # Notifica il client del successo dell'autenticazione
    await websocket.send(json.dumps({
        "type": "AUTH_SUCCESS",
        "payload": {"username": authenticated_username}
    }))
    
    current_room = None
    ws_obj = websocket._get_current_object()  # type: ignore
    
    try:
        # Rate limiting per-connessione (Sliding Window)
        MAX_MESSAGES_PER_SECOND = 30
        message_timestamps = []
        
        while True:
            # Rimane in ascolto di nuovi messaggi dal client
            raw_data = await websocket.receive()
            
            # Controlla rate limit
            current_time = asyncio.get_event_loop().time()
            message_timestamps = [t for t in message_timestamps if current_time - t < 1.0]
            if len(message_timestamps) >= MAX_MESSAGES_PER_SECOND:
                await ws_obj.send(json.dumps({
                    "type": "ERROR",
                    "payload": {"message": "Spam rilevato. Rate limit superato."}
                }))
                await ws_obj.close(4029)
                return
            message_timestamps.append(current_time)
            
            # Limite dimensione messaggio per prevenire DoS
            if len(raw_data) > MAX_WS_MESSAGE_SIZE:
                await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Messaggio troppo grande"}}))
                continue
            
            data = json.loads(raw_data)
            
            msg_type = data.get("type")
            payload = data.get("payload", {})
            room_id = payload.get("roomId")
            
            # 1. GESTIONE INGRESSO STANZA
            if msg_type == "JOIN_ROOM" and room_id:
                # Previene tentativi di join multipli nella stessa sessione WebSocket
                if current_room is not None:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Hai già effettuato l'accesso a una stanza in questa connessione"}
                    }))
                    continue
                # Usa l'identità verificata dal JWT, ignora userId/username dal client
                username = authenticated_username
                user_id = authenticated_user_id
                
                # Verifica che l'utente sia membro della campagna
                try:
                    campaign_id = int(room_id)
                    # Protezione contro int overflow / DB Out of Range DoS (limite max INT a 32 bit in Postgres)
                    if not (1 <= campaign_id <= 2147483647):
                        raise ValueError("ID campagna fuori range")
                except (ValueError, TypeError):
                    await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "ID campagna non valido"}}))
                    continue

                is_member, is_master = await _check_campaign_membership(user_id, campaign_id)
                user_is_master = is_master
                if not is_member:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Non sei membro di questa campagna"}
                    }))
                    await ws_obj.close(4003)
                    return
                
                current_room = room_id
                
                if current_room not in connected_rooms:
                    connected_rooms[current_room] = {}
                
                # Aggiunge il WebSocket corrente alla lista della stanza
                connected_rooms[current_room][ws_obj] = {
                    "username": username,
                    "user_id": user_id,
                    "is_master": is_master
                }
                logger.info(f"-> Utente {username} entrato nella stanza: {current_room}")
                
                # Avvio del loop in background per il salvataggio se non ancora attivo
                global background_loop_task
                if background_loop_task is None or background_loop_task.done():
                    background_loop_task = asyncio.create_task(_save_dirty_campaigns_loop())

                # Carica lo stato della stanza dal database se non è ancora caricato in memoria
                if current_room not in room_tokens:
                    campaign = await Campaign.get_or_none(id=campaign_id)
                    if campaign:
                        room_chat_history[current_room] = campaign.chat_history if campaign.chat_history is not None else []
                        room_tokens[current_room] = campaign.tokens if campaign.tokens is not None else {
                            "test-token": {"x": 200, "y": 200, "color": 0xa855f7}
                        }
                        if campaign.grid_settings is not None:
                            room_grid_settings[current_room] = campaign.grid_settings
                        if campaign.current_map_url is not None:
                            room_current_map[current_room] = campaign.current_map_url
                        if campaign.templates is not None:
                            room_templates[current_room] = campaign.templates
                        if campaign.walls is not None:
                            room_walls[current_room] = campaign.walls
                        room_fow_enabled[current_room] = campaign.fow_enabled
                        room_los_enabled[current_room] = campaign.los_enabled
                        room_initiative[current_room] = campaign.initiative if campaign.initiative is not None else {
                            "active": False,
                            "round": 1,
                            "active_idx": 0,
                            "order": []
                        }

                        # Inizializza le scene se non presenti nel DB
                        if campaign.scenes and isinstance(campaign.scenes, list) and len(campaign.scenes) > 0:
                            room_scenes[current_room] = campaign.scenes
                            room_active_scene_id[current_room] = campaign.active_scene_id or campaign.scenes[0]["id"]
                        else:
                            def_scene_id = "scene-default-1"
                            def_scene = {
                                "id": def_scene_id,
                                "name": "Mappa Principale",
                                "url": campaign.current_map_url or "",
                                "grid_settings": campaign.grid_settings or {"gridSize": 60, "gridColumns": 50, "snapToGrid": True, "gridVisible": True},
                                "tokens": campaign.tokens or {},
                                "walls": campaign.walls or [],
                                "templates": campaign.templates or [],
                                "fow_enabled": campaign.fow_enabled if campaign.fow_enabled is not None else True,
                                "los_enabled": campaign.los_enabled if campaign.los_enabled is not None else True,
                                "is_active": True
                            }
                            room_scenes[current_room] = [def_scene]
                            room_active_scene_id[current_room] = def_scene_id
                    else:
                        room_chat_history[current_room] = []
                        room_tokens[current_room] = {
                            "test-token": {"x": 200, "y": 200, "color": 0xa855f7}
                        }
                        room_initiative[current_room] = {
                            "active": False,
                            "round": 1,
                            "active_idx": 0,
                            "order": []
                        }
                        def_scene_id = "scene-default-1"
                        def_scene = {
                            "id": def_scene_id,
                            "name": "Mappa Principale",
                            "url": "",
                            "grid_settings": {"gridSize": 60, "gridColumns": 50, "snapToGrid": True, "gridVisible": True},
                            "tokens": room_tokens[current_room],
                            "walls": [],
                            "templates": [],
                            "fow_enabled": True,
                            "los_enabled": True,
                            "is_active": True
                        }
                        room_scenes[current_room] = [def_scene]
                        room_active_scene_id[current_room] = def_scene_id

                # Invia l'elenco delle scene della stanza
                if current_room in room_scenes:
                    scenes_message = json.dumps({
                        "type": "UPDATE_SCENES",
                        "payload": {
                            "scenes": room_scenes[current_room],
                            "activeSceneId": room_active_scene_id.get(current_room)
                        }
                    })
                    await ws_obj.send(scenes_message)

                    
                # Invia lo storico della chat al nuovo utente (filtrando i tiri privati per gli utenti non autorizzati)
                filtered_history = []
                for msg in room_chat_history.get(current_room, []):
                    if isinstance(msg, dict) and (msg.get("isPrivate") or msg.get("is_private")):
                        msg_user_id = msg.get("userId")
                        if is_master or (msg_user_id and str(msg_user_id) == str(user_id)):
                            filtered_history.append(msg)
                    else:
                        filtered_history.append(msg)

                history_message = json.dumps({
                    "type": "CHAT_HISTORY",
                    "payload": {"messages": filtered_history}
                })
                await ws_obj.send(history_message)
                
                # Invia i token correnti (filtrando quelli nascosti per i giocatori semplici)
                tokens_to_send = {}
                for t_id, t_data in room_tokens[current_room].items():
                    if is_master or (not t_data.get("is_hidden") and not t_data.get("hidden")) or (str(t_data.get("owner_id")) == str(user_id)):
                        tokens_to_send[t_id] = t_data

                tokens_message = json.dumps({
                    "type": "SYNC_TOKENS",
                    "payload": {"tokens": tokens_to_send}
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
                
                # Invia i template correnti se presenti
                if current_room in room_templates:
                    templates_message = json.dumps({
                        "type": "UPDATE_TEMPLATES",
                        "payload": {"templates": room_templates[current_room]}
                    })
                    await ws_obj.send(templates_message)

                # Invia i muri correnti se presenti
                if current_room in room_walls:
                    walls_message = json.dumps({
                        "type": "UPDATE_WALLS",
                        "payload": {"walls": room_walls[current_room]}
                    })
                    await ws_obj.send(walls_message)

                # Invia le impostazioni della nebbia di guerra e LoS se presenti
                fow_message = json.dumps({
                    "type": "UPDATE_FOW_SETTINGS",
                    "payload": {
                        "enabled": room_fow_enabled.get(current_room, True),
                        "losEnabled": room_los_enabled.get(current_room, True)
                    }
                })
                await ws_obj.send(fow_message)
                
                # Inizializza lo stato dell'iniziativa per la stanza se non esiste (MVP)
                if current_room not in room_initiative:
                    room_initiative[current_room] = {
                        "active": False,
                        "round": 1,
                        "active_idx": 0,
                        "order": []
                    }
                
                # Invia lo stato dell'iniziativa corrente al nuovo utente (filtrato per ruolo)
                init_state = room_initiative[current_room]
                if is_master:
                    await ws_obj.send(json.dumps({
                        "type": "UPDATE_INITIATIVE",
                        "payload": init_state
                    }))
                else:
                    full_order = init_state.get("order", [])
                    filtered_order = []
                    player_active_idx = None
                    for idx, item in enumerate(full_order):
                        if item.get("is_visible", True):
                            filtered_order.append(item)
                            if idx == init_state.get("active_idx"):
                                player_active_idx = len(filtered_order) - 1
                        else:
                            if idx == init_state.get("active_idx"):
                                player_active_idx = None
                    player_state = {
                        "active": init_state.get("active", False),
                        "round": init_state.get("round", 1),
                        "active_idx": player_active_idx,
                        "order": filtered_order
                    }
                    await ws_obj.send(json.dumps({
                        "type": "UPDATE_INITIATIVE",
                        "payload": player_state
                    }))
                
                # Broadcast della lista giocatori aggiornata
                players = [{"username": info["username"], "is_master": info["is_master"], "user_id": info.get("user_id")} if isinstance(info, dict) else {"username": info, "is_master": False, "user_id": None} for info in connected_rooms[current_room].values()]
                broadcast_message = json.dumps({
                    "type": "UPDATE_PLAYERS",
                    "payload": {"players": players}
                })
                await _broadcast(current_room, broadcast_message)
            
            # 2. GESTIONE MOVIMENTO TOKEN (BROADCAST)
            elif msg_type == "MOVE_TOKEN" and current_room:
                token_id = payload.get("tokenId")
                
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
                
                is_authorized = False
                if token_id and isinstance(token_id, str) and len(token_id) <= 50:
                    if current_room not in room_tokens:
                        room_tokens[current_room] = {}
                    
                    # Sanifica coordinate x, y per evitare valori estremi
                    x_val = payload.get("x", 0)
                    y_val = payload.get("y", 0)
                    if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
                        if -10000 <= x_val <= 10000 and -10000 <= y_val <= 10000:
                            if token_id not in room_tokens[current_room]:
                                # Creazione nuovo token
                                # Preventivo DoS: limite massimo di token per stanza
                                if len(room_tokens[current_room]) < 200:
                                    if not is_master:
                                        # I player semplici possono creare al massimo 3 token
                                        owned_count = sum(
                                            1 for t in room_tokens[current_room].values()
                                            if str(t.get("owner_id")) == str(user_id)
                                        )
                                        if owned_count < 3:
                                            is_authorized = True
                                            payload["owner_id"] = user_id
                                    else:
                                        # Il master non ha limiti
                                        is_authorized = True
                            else:
                                # Spostamento token esistente: permesso al proprietario o al master
                                owner_id = room_tokens[current_room][token_id].get("owner_id")
                                is_owner = (str(owner_id) == str(user_id)) if owner_id and user_id else False
                                if is_master or is_owner:
                                    is_authorized = True
 
                if is_authorized:
                    if token_id not in room_tokens[current_room]:
                        new_owner_id = payload.get("owner_id")
                        room_tokens[current_room][token_id] = {
                            "color": payload.get("color", 0xa855f7),
                            "owner_id": new_owner_id
                        }
                    room_tokens[current_room][token_id]["x"] = payload.get("x", 0)
                    room_tokens[current_room][token_id]["y"] = payload.get("y", 0)
                    if "auraRadius" in payload:
                        room_tokens[current_room][token_id]["auraRadius"] = payload.get("auraRadius")
                    if "auraColor" in payload:
                        room_tokens[current_room][token_id]["auraColor"] = payload.get("auraColor")
                    if "visionRadius" in payload:
                        room_tokens[current_room][token_id]["visionRadius"] = payload.get("visionRadius")
                    if "url_avatar" in payload:
                        url_avatar_val = payload.get("url_avatar")
                        if url_avatar_val is None or (isinstance(url_avatar_val, str) and is_safe_url(url_avatar_val)):
                            room_tokens[current_room][token_id]["url_avatar"] = url_avatar_val

                    if "is_hidden" in payload and is_master:
                        room_tokens[current_room][token_id]["is_hidden"] = bool(payload.get("is_hidden"))
                    if "hidden" in payload and is_master:
                        room_tokens[current_room][token_id]["hidden"] = bool(payload.get("hidden"))

                    dirty_campaigns.add(current_room)
 
                    # Arricchiamo il payload broadcasted con colore e owner_id per garantire consistenza.
                    # Forza SEMPRE l'owner_id dello stato del server per prevenire spoofing / furti di token.
                    token_data = room_tokens[current_room][token_id]
                    if "color" not in payload and "color" in token_data:
                        payload["color"] = token_data["color"]
                    if "url_avatar" not in payload and "url_avatar" in token_data:
                        payload["url_avatar"] = token_data["url_avatar"]
                    if "is_hidden" in token_data:
                        payload["is_hidden"] = token_data["is_hidden"]
                    
                    payload["owner_id"] = token_data.get("owner_id")
 
                    broadcast_message = json.dumps({
                        "type": "MOVE_TOKEN",
                        "payload": payload
                    })

                    # Se il token è nascosto, invia solo ai Master e al proprietario del token
                    if token_data.get("is_hidden") or token_data.get("hidden"):
                        for client_ws, client_info in list(connected_rooms.get(current_room, {}).items()):
                            if client_ws != ws_obj:
                                c_is_master = client_info.get("is_master", False) if isinstance(client_info, dict) else False
                                c_user_id = client_info.get("user_id") if isinstance(client_info, dict) else None
                                if c_is_master or (c_user_id and str(c_user_id) == str(token_data.get("owner_id"))):
                                    try:
                                        await asyncio.wait_for(client_ws.send(broadcast_message), timeout=2.0)
                                    except Exception:
                                        pass
                    else:
                        await _broadcast(current_room, broadcast_message, exclude=ws_obj)
                else:
                    # Notifica il client del fallimento dell'autorizzazione
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Non autorizzato a muovere questo token"}
                    }))
                    # Forza un sync dei token per ripristinare lo stato corretto sul client
                    if current_room in room_tokens:
                        await ws_obj.send(json.dumps({
                            "type": "SYNC_TOKENS",
                            "payload": {"tokens": room_tokens[current_room]}
                        }))
                        
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
                            dirty_campaigns.add(current_room)
                            
                            broadcast_message = json.dumps({
                                "type": "REMOVE_TOKEN",
                                "payload": {"tokenId": token_id}
                            })
                            await _broadcast(current_room, broadcast_message)
                        else:
                            await ws_obj.send(json.dumps({
                                "type": "ERROR",
                                "payload": {"message": "Non autorizzato a rimuovere questo token"}
                            }))
                            await ws_obj.send(json.dumps({
                                "type": "SYNC_TOKENS",
                                "payload": {"tokens": room_tokens[current_room]}
                            }))
                        
            # 3. CHAT MESSAGE (BROADCAST)
            elif msg_type == "CHAT_MESSAGE" and current_room:
                if current_room not in room_chat_history:
                    room_chat_history[current_room] = []

                if isinstance(payload, dict):
                    # Validazione e whitelisting (M1)
                    text_val = payload.get("text")
                    if text_val is None:
                        text_val = ""
                    else:
                        text_val = str(text_val)[:2000] # Limite a 2000 caratteri

                    is_private_val = bool(payload.get("isPrivate") or payload.get("is_private") or payload.get("rollVisibility") == "private")

                    sanitized_chat = {
                        "id": payload.get("id", int(asyncio.get_event_loop().time() * 1000)),
                        "roomId": str(payload.get("roomId", ""))[:50],
                        "sender": authenticated_username,
                        "userId": authenticated_user_id,
                        "text": text_val,
                        "isSystem": bool(payload.get("isSystem", False)),
                        "isRoll": bool(payload.get("isRoll", False)),
                        "isCard": bool(payload.get("isCard", False)),
                        "isPrivate": is_private_val,
                    }

                    if "cardTitle" in payload:
                        sanitized_chat["cardTitle"] = str(payload.get("cardTitle", ""))[:100]

                    if sanitized_chat["isRoll"] and isinstance(payload.get("rollDetails"), dict):
                        rd = payload["rollDetails"]
                        formula = str(rd.get("formula", ""))[:50]
                        sources = rd.get("sources")
                        
                        # Evaluate / Roll expression on the server to prevent faking rolls (anti-cheat)
                        server_roll = roll_expression(formula, sources)
                        
                        sanitized_chat["rollDetails"] = {
                            "formula": formula,
                            "results": server_roll["results"],
                            "modifier": server_roll["modifier"],
                            "total": server_roll["total"],
                            "faces": server_roll["faces"]
                        }
                        
                        if sources:
                            sanitized_chat["rollDetails"]["sources"] = server_roll["sources"]

                        # Anti-cheat Initiative Auto-Update
                        # Se il tiro è per l'Iniziativa, il server calcola il valore reale e aggiorna direttamente il tracker
                        reason = text_val
                        if reason and (reason == "Tiro per l'Iniziativa" or "iniziativa" in reason.lower()):
                            # Trova il token posseduto dall'utente in questa stanza
                            owned_token_id = None
                            owned_token_name = None
                            
                            if current_room in room_tokens:
                                for t_id, t_data in room_tokens[current_room].items():
                                    if str(t_data.get("owner_id")) == str(authenticated_user_id):
                                        owned_token_id = t_id
                                        owned_token_name = t_data.get("name")
                                        break
                                        
                            if current_room not in room_initiative:
                                room_initiative[current_room] = {
                                    "active": False,
                                    "round": 1,
                                    "active_idx": 0,
                                    "order": []
                                }
                                
                            initiative_total = server_roll["total"]
                            order = room_initiative[current_room]["order"]
                            updated = False
                            
                            if owned_token_id:
                                for row in order:
                                    if row.get("tokenId") == owned_token_id:
                                        row["initiative"] = initiative_total
                                        updated = True
                                        break
                                if not updated:
                                    order.append({
                                        "tokenId": owned_token_id,
                                        "name": owned_token_name or authenticated_username,
                                        "initiative": initiative_total
                                    })
                            else:
                                player_row_id = f"player-{authenticated_user_id}"
                                for row in order:
                                    if row.get("id") == player_row_id:
                                        row["initiative"] = initiative_total
                                        updated = True
                                        break
                                if not updated:
                                    order.append({
                                        "id": player_row_id,
                                        "name": authenticated_username,
                                        "initiative": initiative_total
                                    })
                                    
                            order.sort(key=lambda x: x.get("initiative", 0), reverse=True)
                            dirty_campaigns.add(current_room)
                            await _broadcast_initiative(current_room)

                    room_chat_history[current_room].append(sanitized_chat)
                    if len(room_chat_history[current_room]) > 100:
                        room_chat_history[current_room] = room_chat_history[current_room][-100:]
                    
                    dirty_campaigns.add(current_room)
                        
                    broadcast_message = json.dumps({
                        "type": "CHAT_MESSAGE",
                        "payload": sanitized_chat
                    })

                    # Se il messaggio/tiro è privato, invia SOLO all'autore e ai Master della stanza
                    if is_private_val:
                        for client_ws, client_info in list(connected_rooms.get(current_room, {}).items()):
                            c_is_master = client_info.get("is_master", False) if isinstance(client_info, dict) else False
                            c_user_id = client_info.get("user_id") if isinstance(client_info, dict) else None
                            if c_is_master or (c_user_id and str(c_user_id) == str(authenticated_user_id)):
                                try:
                                    await asyncio.wait_for(client_ws.send(broadcast_message), timeout=2.0)
                                except Exception:
                                    pass
                    else:
                        await _broadcast(current_room, broadcast_message)
                    
            # 4. GRID SETTINGS (BROADCAST)
            elif msg_type == "GRID_SETTINGS" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master and isinstance(payload, dict):
                    # Whitelist e sanitizzazione (M4)
                    sanitized_grid = {
                        "snapToGrid": bool(payload.get("snapToGrid", True)),
                        "gridVisible": bool(payload.get("gridVisible", True)),
                        "gridColumns": int(payload.get("gridColumns", 20)),
                        "gridSize": int(payload.get("gridSize", 70)),
                        "measurementUnit": str(payload.get("measurementUnit", "feet"))[:10],
                        "diagonalMeasurement": str(payload.get("diagonalMeasurement", "dnd5e"))[:20]
                    }
                    
                    room_grid_settings[current_room] = sanitized_grid
                    dirty_campaigns.add(current_room)
                    
                    broadcast_message = json.dumps({
                        "type": "GRID_SETTINGS",
                        "payload": sanitized_grid
                    })
                    await _broadcast(current_room, broadcast_message)
            
            # 4.5 UPDATE RULER (BROADCAST)
            elif msg_type == "UPDATE_RULER" and current_room:
                if isinstance(payload, dict):
                    # Whitelist e sanitizzazione (M4)
                    waypoints = payload.get("waypoints", [])
                    if isinstance(waypoints, list):
                        sanitized_waypoints = []
                        for pt in waypoints[:100]:
                            if isinstance(pt, list) and len(pt) >= 2:
                                try:
                                    sanitized_waypoints.append([float(pt[0]), float(pt[1])])
                                except (ValueError, TypeError):
                                    continue
                        waypoints = sanitized_waypoints
                    else:
                        waypoints = []
                        
                    sanitized_ruler = {
                        "startX": float(payload.get("startX", 0)),
                        "startY": float(payload.get("startY", 0)),
                        "endX": float(payload.get("endX", 0)),
                        "endY": float(payload.get("endY", 0)),
                        "visible": bool(payload.get("visible", False)),
                        "rulerType": str(payload.get("rulerType", "ray"))[:20],
                        "rayWidth": float(payload.get("rayWidth", 1.0)),
                        "waypoints": waypoints,
                        "username": authenticated_username,
                        "userId": authenticated_user_id
                    }
                    
                    broadcast_message = json.dumps({
                        "type": "UPDATE_RULER",
                        "payload": sanitized_ruler
                    })
                    await _broadcast(current_room, broadcast_message, exclude=ws_obj)
 
            # 5. SET MAP (BROADCAST)
            elif msg_type == "SET_MAP" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if not is_master:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Solo il Master della campagna può cambiare la mappa."}
                    }))
                    continue

                if isinstance(payload, dict):
                    url_val = payload.get("url")
                    if url_val is None:
                        url_val = ""
                    else:
                        url_val = str(url_val)[:2048]
                    
                    if url_val and not is_safe_url(url_val):
                        await ws_obj.send(json.dumps({
                            "type": "ERROR",
                            "payload": {"message": "URL mappa non sicuro o non valido"}
                        }))
                        continue
                        
                    room_current_map[current_room] = url_val
                    curr_act_id = room_active_scene_id.get(current_room)
                    if curr_act_id and current_room in room_scenes:
                        for s in room_scenes[current_room]:
                            if s.get("id") == curr_act_id:
                                s["url"] = url_val
                                break
                    dirty_campaigns.add(current_room)
                    
                    broadcast_message = json.dumps({
                        "type": "SET_MAP",
                        "payload": {"url": url_val}
                    })
                    await _broadcast(current_room, broadcast_message)

                    if current_room in room_scenes:
                        await _broadcast(current_room, json.dumps({
                            "type": "UPDATE_SCENES",
                            "payload": {
                                "scenes": room_scenes[current_room],
                                "activeSceneId": room_active_scene_id.get(current_room)
                            }
                        }))
  
            # 6. CLEAR MAP (BROADCAST)
            elif msg_type == "CLEAR_MAP" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_tokens[current_room] = {}
                    # Pulisce anche i template persistenti quando si ripulisce la mappa
                    room_templates[current_room] = []
                    dirty_campaigns.add(current_room)
                    
                    broadcast_message = json.dumps({
                        "type": "CLEAR_MAP",
                        "payload": {}
                    })
                    await _broadcast(current_room, broadcast_message)
                    
                    # Pulisce anche i template per tutti
                    broadcast_templates = json.dumps({
                        "type": "UPDATE_TEMPLATES",
                        "payload": {"templates": []}
                    })
                    await _broadcast(current_room, broadcast_templates)

            # 6b. CREATE SCENE (MASTER ONLY, ANTI-DOS CLAMPED TO MAX 20 SCENES)
            elif msg_type == "CREATE_SCENE" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master_check = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                if not is_master_check:
                    await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Solo il Master può creare nuove scene."}}))
                    continue

                scenes_list = room_scenes.setdefault(current_room, [])
                if len(scenes_list) >= 20:
                    await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Raggiunto il limite massimo di 20 scene per campagna."}}))
                    continue

                if isinstance(payload, dict):
                    scene_name = str(payload.get("name", "Nuova Scena"))[:100].strip() or "Nuova Scena"
                    map_url = str(payload.get("url", ""))[:2048]
                    if map_url and not is_safe_url(map_url):
                        await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "URL mappa non sicuro."}}))
                        continue

                    # Salva lo stato della vecchia scena attiva
                    curr_active_id = room_active_scene_id.get(current_room)
                    if curr_active_id:
                        for s in scenes_list:
                            if s.get("id") == curr_active_id:
                                s["url"] = room_current_map.get(current_room, "")
                                s["grid_settings"] = room_grid_settings.get(current_room, {})
                                s["tokens"] = room_tokens.get(current_room, {})
                                s["walls"] = room_walls.get(current_room, [])
                                s["templates"] = room_templates.get(current_room, [])
                                s["fow_enabled"] = room_fow_enabled.get(current_room, True)
                                s["los_enabled"] = room_los_enabled.get(current_room, True)
                                s["is_active"] = False
                                break

                    new_scene_id = f"scene-{int(asyncio.get_event_loop().time() * 1000)}"
                    new_scene = {
                        "id": new_scene_id,
                        "name": scene_name,
                        "url": map_url,
                        "grid_settings": {"gridSize": 60, "gridColumns": 50, "snapToGrid": True, "gridVisible": True},
                        "tokens": {},
                        "walls": [],
                        "templates": [],
                        "fow_enabled": True,
                        "los_enabled": True,
                        "is_active": True
                    }
                    scenes_list.append(new_scene)
                    room_active_scene_id[current_room] = new_scene_id

                    # Aggiorna lo stato in memoria della stanza
                    room_current_map[current_room] = map_url
                    room_grid_settings[current_room] = new_scene["grid_settings"]
                    room_tokens[current_room] = {}
                    room_walls[current_room] = []
                    room_templates[current_room] = []
                    room_fow_enabled[current_room] = True
                    room_los_enabled[current_room] = True

                    dirty_campaigns.add(current_room)

                    # Broadcast elenco scene aggiornato
                    await _broadcast(current_room, json.dumps({
                        "type": "UPDATE_SCENES",
                        "payload": {
                            "scenes": scenes_list,
                            "activeSceneId": new_scene_id
                        }
                    }))

                    # Broadcast switch alla nuova scena attiva
                    await _broadcast(current_room, json.dumps({
                        "type": "SWITCH_SCENE",
                        "payload": {
                            "sceneId": new_scene_id,
                            "name": scene_name,
                            "url": map_url,
                            "gridSettings": new_scene["grid_settings"],
                            "tokens": {},
                            "walls": [],
                            "templates": [],
                            "fowEnabled": True,
                            "losEnabled": True
                        }
                    }))

            # 6c. SWITCH SCENE (MASTER ONLY)
            elif msg_type == "SWITCH_SCENE" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master_check = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                if not is_master_check:
                    await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Solo il Master può attivare una scena."}}))
                    continue

                if isinstance(payload, dict):
                    target_scene_id = str(payload.get("sceneId", ""))
                    scenes_list = room_scenes.get(current_room, [])
                    
                    # Salva lo stato della scena corrente prima dello switch
                    curr_active_id = room_active_scene_id.get(current_room)
                    if curr_active_id:
                        for s in scenes_list:
                            if s.get("id") == curr_active_id:
                                s["url"] = room_current_map.get(current_room, "")
                                s["grid_settings"] = room_grid_settings.get(current_room, {})
                                s["tokens"] = room_tokens.get(current_room, {})
                                s["walls"] = room_walls.get(current_room, [])
                                s["templates"] = room_templates.get(current_room, [])
                                s["fow_enabled"] = room_fow_enabled.get(current_room, True)
                                s["los_enabled"] = room_los_enabled.get(current_room, True)
                                s["is_active"] = False
                                break

                    # Cerca la nuova scena
                    target_scene = next((s for s in scenes_list if s.get("id") == target_scene_id), None)
                    if not target_scene:
                        await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Scena richiesta non trovata."}}))
                        continue

                    target_scene["is_active"] = True
                    room_active_scene_id[current_room] = target_scene_id

                    # Carica lo stato della nuova scena nella memoria della stanza
                    room_current_map[current_room] = target_scene.get("url", "")
                    room_grid_settings[current_room] = target_scene.get("grid_settings", {"gridSize": 60, "gridColumns": 50, "snapToGrid": True, "gridVisible": True})
                    room_tokens[current_room] = target_scene.get("tokens", {})
                    room_walls[current_room] = target_scene.get("walls", [])
                    room_templates[current_room] = target_scene.get("templates", [])
                    room_fow_enabled[current_room] = target_scene.get("fow_enabled", True)
                    room_los_enabled[current_room] = target_scene.get("los_enabled", True)

                    dirty_campaigns.add(current_room)

                    # Broadcast elenco scene aggiornato
                    await _broadcast(current_room, json.dumps({
                        "type": "UPDATE_SCENES",
                        "payload": {
                            "scenes": scenes_list,
                            "activeSceneId": target_scene_id
                        }
                    }))

                    # Broadcast stato nuova scena attiva per sincronizzare tutti i client
                    await _broadcast(current_room, json.dumps({
                        "type": "SWITCH_SCENE",
                        "payload": {
                            "sceneId": target_scene_id,
                            "name": target_scene.get("name", "Scena"),
                            "url": target_scene.get("url", ""),
                            "gridSettings": room_grid_settings[current_room],
                            "tokens": room_tokens[current_room],
                            "walls": room_walls[current_room],
                            "templates": room_templates[current_room],
                            "fowEnabled": room_fow_enabled[current_room],
                            "losEnabled": room_los_enabled[current_room]
                        }
                    }))

            # 6d. DELETE SCENE (MASTER ONLY)
            elif msg_type == "DELETE_SCENE" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master_check = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                if not is_master_check:
                    await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Solo il Master può eliminare scene."}}))
                    continue

                if isinstance(payload, dict):
                    scene_id_to_delete = str(payload.get("sceneId", ""))
                    scenes_list = room_scenes.get(current_room, [])
                    if len(scenes_list) <= 1:
                        await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Impossibile eliminare l'unica scena rimasta nella campagna."}}))
                        continue

                    is_deleting_active = (scene_id_to_delete == room_active_scene_id.get(current_room))
                    remaining_scenes = [s for s in scenes_list if s.get("id") != scene_id_to_delete]
                    room_scenes[current_room] = remaining_scenes

                    if is_deleting_active and remaining_scenes:
                        next_scene = remaining_scenes[0]
                        next_scene["is_active"] = True
                        target_scene_id = next_scene["id"]
                        room_active_scene_id[current_room] = target_scene_id

                        room_current_map[current_room] = next_scene.get("url", "")
                        room_grid_settings[current_room] = next_scene.get("grid_settings", {"gridSize": 60, "gridColumns": 50, "snapToGrid": True, "gridVisible": True})
                        room_tokens[current_room] = next_scene.get("tokens", {})
                        room_walls[current_room] = next_scene.get("walls", [])
                        room_templates[current_room] = next_scene.get("templates", [])
                        room_fow_enabled[current_room] = next_scene.get("fow_enabled", True)
                        room_los_enabled[current_room] = next_scene.get("los_enabled", True)

                        dirty_campaigns.add(current_room)

                        await _broadcast(current_room, json.dumps({
                            "type": "UPDATE_SCENES",
                            "payload": {
                                "scenes": remaining_scenes,
                                "activeSceneId": target_scene_id
                            }
                        }))

                        await _broadcast(current_room, json.dumps({
                            "type": "SWITCH_SCENE",
                            "payload": {
                                "sceneId": target_scene_id,
                                "name": next_scene.get("name", "Scena"),
                                "url": next_scene.get("url", ""),
                                "gridSettings": room_grid_settings[current_room],
                                "tokens": room_tokens[current_room],
                                "walls": room_walls[current_room],
                                "templates": room_templates[current_room],
                                "fowEnabled": room_fow_enabled[current_room],
                                "losEnabled": room_los_enabled[current_room]
                            }
                        }))
                    else:
                        dirty_campaigns.add(current_room)
                        await _broadcast(current_room, json.dumps({
                            "type": "UPDATE_SCENES",
                            "payload": {
                                "scenes": remaining_scenes,
                                "activeSceneId": room_active_scene_id.get(current_room)
                            }
                        }))

            # 6e. RENAME SCENE (MASTER ONLY)
            elif msg_type == "RENAME_SCENE" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master_check = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                if not is_master_check:
                    await ws_obj.send(json.dumps({"type": "ERROR", "payload": {"message": "Solo il Master può rinominare le scene."}}))
                    continue

                if isinstance(payload, dict):
                    scene_id = str(payload.get("sceneId", ""))
                    new_name = str(payload.get("name", ""))[:100].strip()
                    if new_name and scene_id:
                        for s in room_scenes.get(current_room, []):
                            if s.get("id") == scene_id:
                                s["name"] = new_name
                                break
                        dirty_campaigns.add(current_room)
                        await _broadcast(current_room, json.dumps({
                            "type": "UPDATE_SCENES",
                            "payload": {
                                "scenes": room_scenes.get(current_room, []),
                                "activeSceneId": room_active_scene_id.get(current_room)
                            }
                        }))
            
            # 7. UPDATE TEMPLATES (BROADCAST WITH SECURITY VALIDATION)
            elif msg_type == "UPDATE_TEMPLATES" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
                username = user_info.get("username") if isinstance(user_info, dict) else "Anonimo"

                if current_room not in room_templates:
                    room_templates[current_room] = []

                new_templates = payload.get("templates", [])
                
                if not isinstance(new_templates, list) or len(new_templates) > 100:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Dati dei template non validi o limite superato"}
                    }))
                    continue

                is_valid = True
                old_templates = room_templates[current_room]
                
                if not is_master:
                    old_map = {t.get("id"): t for t in old_templates if isinstance(t, dict) and t.get("id")}
                    new_map = {t.get("id"): t for t in new_templates if isinstance(t, dict) and t.get("id")}

                    # 1. Impedisce di eliminare le aree degli altri giocatori
                    for tid, ot in old_map.items():
                        if tid not in new_map:
                            owner = ot.get("owner")
                            if str(owner) != str(username):
                                is_valid = False
                                break
                    
                    # 2. Ripristina i dati originali dei template degli altri se inclusi nel payload, 
                    # ed imposta correttamente l'owner per i nuovi template creati
                    if is_valid:
                        validated_templates = []
                        for t in new_templates:
                            if not isinstance(t, dict):
                                continue
                            tid = t.get("id")
                            if tid in old_map:
                                ot = old_map[tid]
                                if str(ot.get("owner")) != str(username):
                                    validated_templates.append(ot)
                                    continue
                            else:
                                t["owner"] = username
                            validated_templates.append(t)
                        new_templates = validated_templates

                if is_valid:
                    sanitized_templates = []
                    for t in new_templates:
                        if not isinstance(t, dict):
                            continue
                        
                        try:
                            sanitized_t = {
                                "id": str(t.get("id", ""))[:50],
                                "startX": float(t.get("startX", 0)),
                                "startY": float(t.get("startY", 0)),
                                "endX": float(t.get("endX", 0)),
                                "endY": float(t.get("endY", 0)),
                                "type": str(t.get("type", "line"))[:20],
                                "width": float(t.get("width", 1.5)),
                                "label": str(t.get("label", ""))[:50],
                                "owner": str(t.get("owner", username))[:50],
                                "color": int(t.get("color", 0x8b5cf6))
                            }
                            sanitized_templates.append(sanitized_t)
                        except (ValueError, TypeError):
                            continue

                    room_templates[current_room] = sanitized_templates
                    dirty_campaigns.add(current_room)
                    
                    broadcast_message = json.dumps({
                        "type": "UPDATE_TEMPLATES",
                        "payload": {"templates": sanitized_templates}
                    })
                    await _broadcast(current_room, broadcast_message)
                else:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Non sei autorizzato a modificare o eliminare le aree degli altri giocatori"}
                    }))
                    await ws_obj.send(json.dumps({
                        "type": "UPDATE_TEMPLATES",
                        "payload": {"templates": room_templates[current_room]}
                    }))
            
            # 8. SEND EMOTE (BROADCAST)
            elif msg_type == "SEND_EMOTE" and current_room:
                emote = payload.get("emote")
                if emote and isinstance(emote, str) and len(emote) <= 10:
                    broadcast_message = json.dumps({
                        "type": "SEND_EMOTE",
                        "payload": {
                            "emote": emote,
                            "username": authenticated_username
                        }
                    })
                    await _broadcast(current_room, broadcast_message)

            # 8.5 PING (BROADCAST)
            elif msg_type == "PING" and current_room:
                if isinstance(payload, dict):
                    x_val = payload.get("x")
                    y_val = payload.get("y")
                    
                    if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
                        if -10000 <= x_val <= 10000 and -10000 <= y_val <= 10000:
                            broadcast_message = json.dumps({
                                "type": "PING",
                                "payload": {
                                    "x": float(x_val),
                                    "y": float(y_val),
                                    "username": authenticated_username,
                                    "color": int(payload.get("color", 0xa855f7))
                                }
                            })
                            await _broadcast(current_room, broadcast_message)

            # 9. UPDATE WALLS (MASTER ONLY)
            elif msg_type == "UPDATE_WALLS" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    new_walls = payload.get("walls", [])
                    if isinstance(new_walls, list) and len(new_walls) <= 300:
                        sanitized_walls = []
                        for w in new_walls:
                            if not isinstance(w, dict):
                                continue
                            try:
                                sanitized_w = {
                                    "id": str(w.get("id", ""))[:50],
                                    "startX": float(w.get("startX", 0)),
                                    "startY": float(w.get("startY", 0)),
                                    "endX": float(w.get("endX", 0)),
                                    "endY": float(w.get("endY", 0))
                                }
                                sanitized_walls.append(sanitized_w)
                            except (ValueError, TypeError):
                                continue
                        
                        room_walls[current_room] = sanitized_walls
                        dirty_campaigns.add(current_room)
                        
                        broadcast_message = json.dumps({
                            "type": "UPDATE_WALLS",
                            "payload": {"walls": sanitized_walls}
                        })
                        await _broadcast(current_room, broadcast_message)
                    else:
                        await ws_obj.send(json.dumps({
                            "type": "ERROR",
                            "payload": {"message": "Dati dei muri non validi o limite superato"}
                        }))
                else:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Solo il Master può aggiornare i muri della campagna"}
                    }))

            # 10. UPDATE FOW SETTINGS (MASTER ONLY)
            elif msg_type == "UPDATE_FOW_SETTINGS" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    enabled = payload.get("enabled", True)
                    los_enabled = payload.get("losEnabled", True)
                    room_fow_enabled[current_room] = enabled
                    room_los_enabled[current_room] = los_enabled
                    dirty_campaigns.add(current_room)
                    
                    broadcast_message = json.dumps({
                        "type": "UPDATE_FOW_SETTINGS",
                        "payload": {
                            "enabled": enabled,
                            "losEnabled": los_enabled
                        }
                    })
                    await _broadcast(current_room, broadcast_message)
                else:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Solo il Master può modificare le impostazioni della nebbia di guerra"}
                    }))

            # 11. UPDATE INITIATIVE STATE (MASTER ONLY)
            elif msg_type == "UPDATE_INITIATIVE_STATE" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    active = payload.get("active", False)
                    round_val = payload.get("round", 1)
                    active_idx = payload.get("active_idx", 0)
                    order = payload.get("order", [])
                    
                    # Security checks: bounds clamping & type validation
                    if not isinstance(active, bool):
                        active = False
                    if not isinstance(round_val, int) or not (1 <= round_val <= 10000):
                        round_val = 1
                    if active_idx is not None:
                        if not isinstance(active_idx, int) or active_idx < 0 or active_idx > 200:
                            active_idx = 0
                    if not isinstance(order, list) or len(order) > 200:
                        order = []
                        
                    sanitized_order = []
                    for item in order:
                        if not isinstance(item, dict):
                            continue
                        name_str = str(item.get("name", ""))[:50]
                        # Remove potentially harmful characters for XSS
                        name_str = name_str.replace("<", "&lt;").replace(">", "&gt;")
                        
                        init_val = item.get("initiative", 0)
                        if not isinstance(init_val, (int, float)):
                            init_val = 0
                        # Clamp initiative values between -100 and 100
                        init_val = max(-100.0, min(100.0, float(init_val)))
                        
                        sanitized_order.append({
                            "id": str(item.get("id", ""))[:50],
                            "tokenId": str(item.get("tokenId", ""))[:50] if item.get("tokenId") else None,
                            "name": name_str,
                            "initiative": init_val,
                            "is_visible": bool(item.get("is_visible", True)),
                            "is_npc": bool(item.get("is_npc", False)),
                            "owner_id": item.get("owner_id")
                        })
                        
                    room_initiative[current_room] = {
                        "active": active,
                        "round": round_val,
                        "active_idx": active_idx,
                        "order": sanitized_order
                    }
                    dirty_campaigns.add(current_room)
                    await _broadcast_initiative(current_room)
                else:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Solo il Master può aggiornare lo stato del combattimento"}
                    }))

            # 12. UPDATE INITIATIVE ROW (MASTER OR TOKEN OWNER)
            elif msg_type == "UPDATE_INITIATIVE_ROW" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                user_id = user_info.get("user_id") if isinstance(user_info, dict) else None
                
                token_id = payload.get("tokenId")
                init_val = payload.get("initiative", 0)
                
                # Validation
                if not isinstance(init_val, (int, float)):
                    init_val = 0
                init_val = max(-100.0, min(100.0, float(init_val)))
                
                if current_room not in room_initiative:
                    room_initiative[current_room] = {
                        "active": False,
                        "round": 1,
                        "active_idx": 0,
                        "order": []
                    }
                
                is_authorized = False
                token_data = None
                
                # Check token ownership in room_tokens
                if token_id and current_room in room_tokens:
                    token_data = room_tokens[current_room].get(token_id)
                    if token_data:
                        owner_id = token_data.get("owner_id")
                        is_owner = (str(owner_id) == str(user_id)) if owner_id and user_id else False
                        if is_master or is_owner:
                            is_authorized = True
                
                # Alternatively, Master can add custom non-token entries
                if is_master and not token_data:
                    is_authorized = True
                    
                if is_authorized:
                    order = room_initiative[current_room].setdefault("order", [])
                    
                    # Find if it already exists by tokenId (or by ID if it's a custom Master entry)
                    existing_item = None
                    target_id = payload.get("id") or token_id
                    
                    for item in order:
                        if (token_id and item.get("tokenId") == token_id) or (target_id and item.get("id") == target_id):
                            existing_item = item
                            break
                            
                    if existing_item:
                        existing_item["initiative"] = init_val
                        # Only GM can change visibility or name
                        if is_master:
                            if "is_visible" in payload:
                                existing_item["is_visible"] = bool(payload["is_visible"])
                            if "name" in payload:
                                name_str = str(payload["name"])[:50].replace("<", "&lt;").replace(">", "&gt;")
                                existing_item["name"] = name_str
                    else:
                        # Check list limit to prevent memory exhaustion DoS
                        if len(order) >= 200:
                            await ws_obj.send(json.dumps({
                                "type": "ERROR",
                                "payload": {"message": "Limite massimo di righe dell'iniziativa superato"}
                            }))
                            continue
                            
                        name_str = str(payload.get("name", "Entità"))[:50].replace("<", "&lt;").replace(">", "&gt;")
                        is_visible = bool(payload.get("is_visible", True)) if is_master else True
                        is_npc = bool(payload.get("is_npc", False)) if is_master else False
                        owner_id = token_data.get("owner_id") if token_data else user_id
                        
                        order.append({
                            "id": str(target_id)[:50],
                            "tokenId": str(token_id)[:50] if token_id else None,
                            "name": name_str,
                            "initiative": init_val,
                            "is_visible": is_visible,
                            "is_npc": is_npc,
                            "owner_id": owner_id
                        })
                        
                    dirty_campaigns.add(current_room)
                    await _broadcast_initiative(current_room)
                else:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Non sei autorizzato ad aggiungere o aggiornare questa iniziativa"}
                    }))

            # 13. REMOVE INITIATIVE ROW (MASTER ONLY)
            elif msg_type == "REMOVE_INITIATIVE_ROW" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    target_id = payload.get("id")
                    token_id = payload.get("tokenId")
                    
                    if current_room in room_initiative:
                        order = room_initiative[current_room].get("order", [])
                        new_order = []
                        for item in order:
                            if (token_id and item.get("tokenId") == token_id) or (target_id and item.get("id") == target_id):
                                continue
                            new_order.append(item)
                        room_initiative[current_room]["order"] = new_order
                        
                        # Adjust active_idx if out of bounds
                        active_idx = room_initiative[current_room].get("active_idx", 0)
                        if active_idx >= len(new_order) and len(new_order) > 0:
                            room_initiative[current_room]["active_idx"] = len(new_order) - 1
                            
                        dirty_campaigns.add(current_room)
                        await _broadcast_initiative(current_room)
                else:
                    await ws_obj.send(json.dumps({
                        "type": "ERROR",
                        "payload": {"message": "Solo il Master può rimuovere righe dall'iniziativa"}
                    }))
  
    except asyncio.CancelledError:
        # Gestisce la disconnessione pulita del browser (es. chiusura scheda)
        raise
    finally:
        # Se il giocatore si disconnette, lo rimuoviamo dalla stanza
        if current_room and current_room in connected_rooms:
            connected_rooms[current_room].pop(ws_obj, None)
            logger.info(f"<- Un utente ha lasciato la stanza: {current_room}")
            
            try:
                players = [{"username": info["username"], "is_master": info["is_master"], "user_id": info.get("user_id")} if isinstance(info, dict) else {"username": info, "is_master": False, "user_id": None} for info in connected_rooms[current_room].values()]
                broadcast_message = json.dumps({
                    "type": "UPDATE_PLAYERS",
                    "payload": {"players": players}
                })
                # Usiamo asyncio.shield e catturiamo eventuali eccezioni per evitare che la disconnessione
                # o la cancellazione improvvisa interrompa il cleanup finale della stanza
                await asyncio.shield(_broadcast(current_room, broadcast_message))
            except Exception as e:
                logger.warning(f"Errore non bloccante nel broadcast finale di disconnessione: {e}")
                
            if not connected_rooms[current_room]:
                del connected_rooms[current_room]
                
                # Salvataggio immediato dello stato finale prima della rimozione dei dati in memoria
                await _save_campaign_state_immediate(current_room)
                if current_room in dirty_campaigns:
                    dirty_campaigns.remove(current_room)
                
                # Cleanup dei dizionari di stato della stanza per evitare memory leak (L4)
                room_chat_history.pop(current_room, None)
                room_grid_settings.pop(current_room, None)
                room_current_map.pop(current_room, None)
                room_tokens.pop(current_room, None)
                room_templates.pop(current_room, None)
                room_walls.pop(current_room, None)
                room_fow_enabled.pop(current_room, None)
                room_los_enabled.pop(current_room, None)
                room_initiative.pop(current_room, None)
                logger.info(f"Pulito completamente lo stato in memoria per la stanza vuota: {current_room}")

