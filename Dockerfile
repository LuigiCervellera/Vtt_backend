FROM python:3.12-slim

WORKDIR /app

# Installa le dipendenze
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice dell'applicazione
COPY . .

# Esponi la porta usata da Quart
EXPOSE 5000

# Avvia l'applicazione con Hypercorn (server ASGI raccomandato per Quart)
# Eseguiamo il modulo main:app sulla porta 5000 bindata su tutti gli indirizzi
CMD ["hypercorn", "main:app", "-b", "0.0.0.0:5000"]
