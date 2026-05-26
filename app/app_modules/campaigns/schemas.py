from dataclasses import dataclass

@dataclass
class CampaignCreate:
    nome_campagna: str
    codice_invito: str

@dataclass
class CampaignJoin:
    codice_invito: str
