"""
Sanitizzazione difensiva di ``scheda_dati`` (JSONField arbitrario) prima del
salvataggio nel database.

``scheda_dati`` contiene l'intera scheda personaggio editabile lato frontend
(statistiche, attacchi, incantesimi, inventario, note libere, ...). Essendo un
``JSONField`` senza schema, un client malevolo può inviare payload arbitrari
(array/oggetti giganti, annidamenti profondi, stringhe enormi) che verrebbero
salvati nel DB e poi trasmessi via WebSocket a tutti i membri della stanza,
causando DoS lato client (tab bloccato) e consumo di banda/DB.

Questa funzione applica limiti conservativi e scarta i tipi non standard.
Se il payload eccede i limiti viene sollevata ``ValueError``, che il chiamante
può convertire in una risposta 400.
"""
from __future__ import annotations

# --- Limiti conservativi ---------------------------------------------------
# Sono pensati per coprire qualsiasi scheda reale di D&D 5e / Pathfinder /
# Warhammer generata dal frontend, ma bloccare abusi evidenti.

MAX_JSON_BYTES = 256 * 1024          # 256 KB — una scheda reale pesa pochi KB
MAX_DEPTH = 8                        # profondità massima di annidamento
MAX_TOTAL_KEYS = 500                 # numero totale di chiavi in tutto l'albero
MAX_ARRAY_LENGTH = 200               # lunghezza massima di ogni singolo array
MAX_STRING_LENGTH = 50_000           # lunghezza massima di ogni stringa
MAX_OBJECT_KEYS = 100                # chiavi per singolo oggetto
# --- -----------------------------------------------------------------------


class SchedaValidationError(ValueError):
    """Sollevata quando ``scheda_dati`` non rispetta i limiti imposti."""


def _validate(node, depth: int, counters: dict) -> None:
    """Visita ricorsivamente ``node`` applicando i limiti definiti sopra."""
    if depth > MAX_DEPTH:
        raise SchedaValidationError(f"Profondità JSON superiore a {MAX_DEPTH}")

    if isinstance(node, dict):
        if len(node) > MAX_OBJECT_KEYS:
            raise SchedaValidationError(
                f"Oggetto con più di {MAX_OBJECT_KEYS} chiavi"
            )
        for key, value in node.items():
            if not isinstance(key, str):
                raise SchedaValidationError("Chiavi oggetto devono essere stringhe")
            if len(key) > MAX_STRING_LENGTH:
                raise SchedaValidationError("Chiave oggetto troppo lunga")
            counters["keys"] += 1
            if counters["keys"] > MAX_TOTAL_KEYS:
                raise SchedaValidationError(
                    f"Numero totale di chiavi superiore a {MAX_TOTAL_KEYS}"
                )
            _validate(value, depth + 1, counters)

    elif isinstance(node, list):
        if len(node) > MAX_ARRAY_LENGTH:
            raise SchedaValidationError(
                f"Array con più di {MAX_ARRAY_LENGTH} elementi"
            )
        for item in node:
            _validate(item, depth + 1, counters)

    elif isinstance(node, str):
        if len(node) > MAX_STRING_LENGTH:
            raise SchedaValidationError("Stringa troppo lunga")

    elif isinstance(node, bool) or isinstance(node, (int, float)) or node is None:
        # Tipi JSON standard, accettati senza limiti (i numeri float possono
        # essere grandi, ma il costo in byte resta limitato).
        return

    else:
        # Qualsiasi altro tipo Python (es. oggetti arbitrari provenienti da
        # deserializzazioni anomale) viene rifiutato.
        raise SchedaValidationError(f"Tipo non supportato: {type(node).__name__}")


def sanitize_scheda_dati(data) -> dict:
    """
    Valida ``data`` e ritorna un ``dict`` sicuro da salvare.

    Solleva :class:`SchedaValidationError` se il payload non è un dict o
    eccede i limiti. Il chiamante deve gestire l'eccezione ritornando HTTP 400.
    """
    if data is None:
        return {}

    if not isinstance(data, dict):
        raise SchedaValidationError("scheda_dati deve essere un oggetto JSON")

    # Controllo preventivo sulla dimensione grezza: blocca payload enormi
    # prima di visitarli ricorsivamente (DoS sulla validazione stessa).
    import json
    try:
        raw_size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise SchedaValidationError("scheda_dati non serializzabile in JSON")
    if raw_size > MAX_JSON_BYTES:
        raise SchedaValidationError(
            f"Payload scheda_dati troppo grande ({raw_size} bytes > {MAX_JSON_BYTES})"
        )

    counters = {"keys": 0}
    _validate(data, depth=1, counters=counters)
    return data
