from dataclasses import dataclass
from typing import Optional

@dataclass
class CharacterCreate:
    campagna_id: int
    nome: str
    is_npc: bool = False
    proprietario_id: Optional[int] = None
    url_avatar: Optional[str] = None
    scheda_dati: Optional[dict] = None

@dataclass
class CharacterUpdate:
    nome: Optional[str] = None
    is_npc: Optional[bool] = None
    url_avatar: Optional[str] = None
    scheda_dati: Optional[dict] = None
