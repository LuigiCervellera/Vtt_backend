import asyncio
from tortoise import Tortoise, fields, Model

class User(Model):
    id = fields.IntField(pk=True)
    username = fields.CharField(max_length=50)

class Campaign(Model):
    id = fields.IntField(pk=True)
    master = fields.ForeignKeyField("models.User")

async def main():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["__main__"]})
    await Tortoise.generate_schemas()
    u = await User.create(username="test")
    c = await Campaign.create(master=u)
    c_fetched = await Campaign.get(id=c.id)
    print(dir(c_fetched))
    print(getattr(c_fetched, 'master_id', 'missing'))

asyncio.run(main())
