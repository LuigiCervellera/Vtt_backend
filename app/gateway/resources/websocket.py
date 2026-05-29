import json
import asyncio
import jwt
import logging

logger = logging.getLogger(__name__)
from quart import Blueprint, websocket
from models import Campaign, User
from app.app_modules.base.config import JWT_SECRET, JWT_ALGORITHM, MAX_WS_MESSAGE_SIZE
from app.app_modules.auth.blacklist import is_blacklisted


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
        
        token = websocket.cookies.get("vtt_token")
        if not token:
            # Fallback per compatibilità/testing
            token = auth_data.get("payload", {}).get("token")
            
        if not token:
            raise ValueError("Token mancante")
    except asyncio.TimeoutError:
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": "Timeout di autenticazione"}}))
        await websocket.close(4003)
        return
    except Exception as e:
        await websocket.send(json.dumps({"type": "ERROR", "payload": {"message": f"Autenticazione fallita: {str(e)}"}}))
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
    if jti and is_blacklisted(jti):
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
                
                # Broadcast della lista giocatori aggiornata
                players = [{"username": info["username"], "is_master": info["is_master"]} if isinstance(info, dict) else {"username": info, "is_master": False} for info in connected_rooms[current_room].values()]
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
 
                    # Arricchiamo il payload broadcasted con colore e owner_id per garantire consistenza.
                    # Forza SEMPRE l'owner_id dello stato del server per prevenire spoofing / furti di token.
                    token_data = room_tokens[current_room][token_id]
                    if "color" not in payload and "color" in token_data:
                        payload["color"] = token_data["color"]
                    
                    payload["owner_id"] = token_data.get("owner_id")

                    broadcast_message = json.dumps({
                        "type": "MOVE_TOKEN",
                        "payload": payload
                    })
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

                # Forza l'identità dal JWT — ignora username/userId dal client
                payload["username"] = authenticated_username
                payload["userId"] = authenticated_user_id

                room_chat_history[current_room].append(payload)
                if len(room_chat_history[current_room]) > 100:
                    room_chat_history[current_room] = room_chat_history[current_room][-100:]
                    
                broadcast_message = json.dumps({
                    "type": "CHAT_MESSAGE",
                    "payload": payload
                })
                
                # Invia a tutti, compreso il mittente, così si assicura che sia stato ricevuto
                await _broadcast(current_room, broadcast_message)
                    
            # 4. GRID SETTINGS (BROADCAST)
            elif msg_type == "GRID_SETTINGS" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_grid_settings[current_room] = payload
                    broadcast_message = json.dumps({
                        "type": "GRID_SETTINGS",
                        "payload": payload
                    })
                    await _broadcast(current_room, broadcast_message)
            
            # 4.5 UPDATE RULER (BROADCAST)
            elif msg_type == "UPDATE_RULER" and current_room:
                payload["username"] = authenticated_username
                payload["userId"] = authenticated_user_id
                
                logger.debug(f"[RULER] {authenticated_username} in room {current_room}: visible={payload.get('visible')}")
                
                broadcast_message = json.dumps({
                    "type": "UPDATE_RULER",
                    "payload": payload
                })
                await _broadcast(current_room, broadcast_message, exclude=ws_obj)
 
            # 5. SET MAP (BROADCAST)
            elif msg_type == "SET_MAP" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_current_map[current_room] = payload.get("url")
                    broadcast_message = json.dumps({
                        "type": "SET_MAP",
                        "payload": payload
                    })
                    await _broadcast(current_room, broadcast_message)
  
            # 6. CLEAR MAP (BROADCAST)
            elif msg_type == "CLEAR_MAP" and current_room:
                user_info = connected_rooms.get(current_room, {}).get(ws_obj, {})
                is_master = user_info.get("is_master", False) if isinstance(user_info, dict) else False
                
                if is_master:
                    room_tokens[current_room] = {}
                    # Pulisce anche i template persistenti quando si ripulisce la mappa
                    room_templates[current_room] = []
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
  
    except asyncio.CancelledError:
        # Gestisce la disconnessione pulita del browser (es. chiusura scheda)
        raise
    finally:
        # Se il giocatore si disconnette, lo rimuoviamo dalla stanza
        if current_room and current_room in connected_rooms:
            connected_rooms[current_room].pop(ws_obj, None)
            logger.info(f"<- Un utente ha lasciato la stanza: {current_room}")
            
            try:
                players = [{"username": info["username"], "is_master": info["is_master"]} if isinstance(info, dict) else {"username": info, "is_master": False} for info in connected_rooms[current_room].values()]
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

