from dataclasses import dataclass

@dataclass
class CampaignCreate:
    nome_campagna: str

@dataclass
class CampaignJoin:
    codice_invito: str
