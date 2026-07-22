FROM python:3.12-slim

RUN groupadd -g 999 appgroup && useradd -r -u 999 -g appgroup appuser

WORKDIR /app

# Installa dipendenze di sistema minime (curl per healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Installa le dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice dell'applicazione e imposta i permessi per la cartella uploads
COPY . .
RUN mkdir -p /app/uploads && chown -R appuser:appgroup /app

USER appuser

# Esponi la porta usata da Quart
EXPOSE 5000

# Healthcheck nativo Docker
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Avvia l'applicazione con Hypercorn (server ASGI raccomandato per Quart)
CMD ["hypercorn", "main:app", "-b", "0.0.0.0:5000"]
