from dataclasses import dataclass

@dataclass
class MapCreate:
    campagna_id: int
    nome_mappa: str
    url_immagine: str
    is_active: bool = False
