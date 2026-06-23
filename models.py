from tortoise.models import Model
from tortoise import fields
from werkzeug.security import generate_password_hash, check_password_hash

class User(Model):
    id = fields.IntField(pk=True)
    username = fields.CharField(max_length=50, unique=True)
    email = fields.CharField(max_length=255, unique=True, null=True)
    password_hash = fields.CharField(max_length=256)
    created_at = fields.DatetimeField(auto_now_add=True)

    # Relazioni
    campaigns_mastered: fields.ReverseRelation["Campaign"]
    campaigns_joined: fields.ManyToManyRelation["Campaign"]
    characters: fields.ReverseRelation["Character"]

    class Meta:  # type: ignore
        table = "users"

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __str__(self):
        return self.username

class Campaign(Model):
    id = fields.IntField(pk=True)
    nome_campagna = fields.CharField(max_length=255)
    codice_invito = fields.CharField(max_length=50, unique=True)
    master: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User", related_name="campaigns_mastered"
    )
    master_id: int

    partecipanti: fields.ManyToManyRelation[User] = fields.ManyToManyField(
        "models.User", related_name="campaigns_joined", through="partecipanti_campagna"
    )

    maps: fields.ReverseRelation["Map"]
    characters: fields.ReverseRelation["Character"]

    system_id = fields.CharField(max_length=50, default="generic")

    class Meta:  # type: ignore
        table = "campaigns"

    def __str__(self):
        return self.nome_campagna

class Map(Model):
    id = fields.IntField(pk=True)
    campagna: fields.ForeignKeyRelation[Campaign] = fields.ForeignKeyField(
        "models.Campaign", related_name="maps"
    )
    campagna_id: int
    nome_mappa = fields.CharField(max_length=255)
    url_immagine = fields.CharField(max_length=1024)
    is_active = fields.BooleanField(default=False)

    class Meta:  # type: ignore
        table = "maps"

    def __str__(self):
        return self.nome_mappa

class Character(Model):
    id = fields.IntField(pk=True)
    campagna: fields.ForeignKeyRelation[Campaign] = fields.ForeignKeyField(
        "models.Campaign", related_name="characters"
    )
    campagna_id: int
    nome = fields.CharField(max_length=255)
    is_npc = fields.BooleanField(default=False)
    proprietario: fields.ForeignKeyRelation[User] | None = fields.ForeignKeyField(
        "models.User", related_name="characters", null=True
    )
    proprietario_id: int | None
    url_avatar = fields.CharField(max_length=1024, null=True)
    scheda_dati = fields.JSONField(default=dict)

    class Meta:  # type: ignore
        table = "characters"

    def __str__(self):
        return self.nome
