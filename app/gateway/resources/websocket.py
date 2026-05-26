import json
import asyncio
import jwt
from quart import Blueprint, websocket
from models import Campaign
from app.app_modules.base.config import JWT_SECRET, JWT_ALGORITHM, MAX_WS_MESSAGE_SIZE
from app.app_modules.base.redis_client import redis_manager

ws_bp = Blueprint("ws", __name__)

# State dictionaries for real-time rooms
connected_rooms = {}
room_chat_history = {}
room_grid_settings = {}
room_current_map = {}
room_tokens = {}

@ws_bp.websocket("/ws")
async def ws_endpoint():
    """
    Questo endpoint gestisce le connessioni WebSocket in tempo reale.
    Richiede autenticazione JWT tramite query parameter ?token=xxx
    """
    # Verifica JWT dall'handshake
    token = websocket.args.get("token")
    if not token:
        await websocket.accept()
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token mancante"}}))
        await websocket.close(1008)
        return
    
    try:
        jwt_payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        await websocket.accept()
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token scaduto"}}))
        await websocket.close(1008)
        return
    except jwt.InvalidTokenError:
        await websocket.accept()
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token non valido"}}))
        await websocket.close(1008)
        return
    
    # Verifica token in Redis (se presente)
    if redis_manager.client:
        exists = await redis_manager.client.exists(f"token:{token}")
        if not exists:
            await websocket.accept()
            await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Token revocato"}}))
            await websocket.close(1008)
            return
    
    # Identità verificata dal server, non dal client
    authenticated_user_id = jwt_payload["id"]
    authenticated_username = jwt_payload["username"]
    
    await websocket.accept()
    
    current_room = None
    ws_obj = websocket._get_current_object()  # type: ignore
    
    try:
        while True:
            # Rimane in ascolto di nuovi messaggi dal client
            raw_data = await websocket.receive()
            
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
                current_room = room_id
                # Usa l'identità verificata dal JWT, ignora userId/username dal client
                username = authenticated_username
                user_id = authenticated_user_id
                
                is_master = False
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
                    try:
                        await client.send(broadcast_message)
                    except Exception:
                        pass
            
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
                        # Creazione nuovo token: permesso a tutti, ma sanifichiamo l'owner_id per i player non-master
                        is_authorized = True
                        if not is_master:
                            payload["ownerId"] = user_id
                            payload["owner_id"] = user_id
                    else:
                        # Spostamento: permesso al creatore o al master
                        owner_id = room_tokens[current_room][token_id].get("owner_id")
                        is_owner = (str(owner_id) == str(user_id)) if owner_id and user_id else False
                        if is_master or is_owner:
                            is_authorized = True

                if is_authorized:
                    if token_id not in room_tokens[current_room]:
                        new_owner_id = payload.get("ownerId", payload.get("owner_id"))
                        room_tokens[current_room][token_id] = {
                            "color": payload.get("color", 0xa855f7),
                            "owner_id": new_owner_id
                        }
                    room_tokens[current_room][token_id]["x"] = payload.get("x", 0)
                    room_tokens[current_room][token_id]["y"] = payload.get("y", 0)

                    # Arricchiamo il payload broadcasted con colore e ownerId per garantire consistenza
                    token_data = room_tokens[current_room][token_id]
                    if "color" not in payload and "color" in token_data:
                        payload["color"] = token_data["color"]
                    if "ownerId" not in payload and "owner_id" in token_data:
                        payload["ownerId"] = token_data["owner_id"]

                    targets = connected_rooms.get(current_room, {}).keys()
                    broadcast_message = json.dumps({
                        "type": "MOVE_TOKEN",
                        "payload": payload
                    })
                    
                    for client in list(targets):
                        if client != ws_obj:
                            try:
                                await client.send(broadcast_message)
                            except Exception:
                                pass
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
                            
                            targets = connected_rooms.get(current_room, {}).keys()
                            broadcast_message = json.dumps({
                                "type": "REMOVE_TOKEN",
                                "payload": {"tokenId": token_id}
                            })
                            for client in list(targets):
                                try:
                                    await client.send(broadcast_message)
                                except Exception:
                                    pass
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
                room_chat_history[current_room].append(payload)
                if len(room_chat_history[current_room]) > 100:
                    room_chat_history[current_room] = room_chat_history[current_room][-100:]
                    
                targets = connected_rooms.get(current_room, {}).keys()
                broadcast_message = json.dumps({
                    "type": "CHAT_MESSAGE",
                    "payload": payload
                })
                
                # Invia a tutti, compreso il mittente, così si assicura che sia stato ricevuto
                for client in list(targets):
                    try:
                        await client.send(broadcast_message)
                    except Exception:
                        pass
                    
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
                    for client in list(targets):
                        try:
                            await client.send(broadcast_message)
                        except Exception:
                            pass
 
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
                    for client in list(targets):
                        try:
                            await client.send(broadcast_message)
                        except Exception:
                            pass
  
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
                    for client in list(targets):
                        try:
                            await client.send(broadcast_message)
                        except Exception:
                            pass
  
    except asyncio.CancelledError:
        # Gestisce la disconnessione pulita del browser (es. chiusura scheda)
        raise
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
                try:
                    await client.send(broadcast_message)
                except Exception:
                    pass
                
            if not connected_rooms[current_room]:
                del connected_rooms[current_room]
