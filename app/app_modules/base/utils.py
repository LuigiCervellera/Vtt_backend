import os
from urllib.parse import urlparse

def is_safe_url(url: str) -> bool:
    """
    Verifica se un URL è sicuro per la visualizzazione dell'avatar o della mappa.
    Consente percorsi relativi sicuri (es. /uploads/...) e URL assoluti http/https.
    Previene l'uso di schemi pericolosi come javascript:, data:, vbscript:.
    """
    if not url:
        return True
    
    # Consente percorsi relativi locali (es. /uploads/...) ma vieta schemi relativi //
    if url.startswith("/") and not url.startswith("//"):
        return not any(url.lower().startswith(s) for s in ["javascript:", "data:", "vbscript:"])
        
    try:
        parsed = urlparse(url)
        # Consente solo http/https
        if parsed.scheme not in ("http", "https"):
            return False
            
        return True
    except Exception:
        return False
