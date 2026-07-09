"""
Blacklist per JWT revocati (logout).

Implementazione persistente nel database PostgreSQL tramite Tortoise ORM.
"""
import datetime

async def blacklist_token(jti: str, expires_in: int = 86400) -> None:
    """Aggiunge un JTI alla blacklist (token revocato) nel DB."""
    from models import BlacklistedToken
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
    await BlacklistedToken.create(jti=jti, expires_at=expires_at)


async def is_blacklisted(jti: str) -> bool:
    """Verifica se un JTI è nella blacklist."""
    from models import BlacklistedToken
    return await BlacklistedToken.filter(jti=jti).exists()
