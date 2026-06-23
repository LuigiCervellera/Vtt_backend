from dataclasses import dataclass

@dataclass
class CampaignCreate:
    nome_campagna: str
    system_id: str = "generic"

@dataclass
class CampaignJoin:
    codice_invito: str
