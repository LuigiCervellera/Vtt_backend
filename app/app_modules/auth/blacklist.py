"""
Blacklist per JWT revocati (logout).

Implementazione in-memory con set di JTI (JWT ID).
In futuro, migrare a Redis per supporto multi-istanza.
"""

# Set di JTI (JWT ID) dei token revocati
_blacklisted_jtis: set[str] = set()


def blacklist_token(jti: str) -> None:
    """Aggiunge un JTI alla blacklist (token revocato)."""
    _blacklisted_jtis.add(jti)


def is_blacklisted(jti: str) -> bool:
    """Verifica se un JTI è nella blacklist."""
    return jti in _blacklisted_jtis
