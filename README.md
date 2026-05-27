# 🐉 VTT Backend (Virtual Table Top)

API REST + WebSocket server per gestire sessioni di gioco, mappe e token in tempo reale.

## 🛠️ Stack Tecnologico

- **Runtime**: Python 3.11+
- **Framework**: Flask / Flask-SocketIO
- **Database**: PostgreSQL 16 (Alpine) - *Niente più SQLite*
- **Containerization**: Docker & Docker Compose
- **Auth**: JWT (Stateless) con validazione su WebSocket handshake e per-message.

## 🚀 Quick Start

### Prerequisiti

- Docker & Docker Compose installati.
- File `.env` configurato (vedi sotto).

### Avvio

Per avviare il backend è necessario utilizzare Docker.

Con **Docker Compose** (Consigliato, avvia sia il backend che il DB):

```bash
# Avvia DB e Backend
docker compose up -d

# Vedi i logs
docker compose logs -f backend
```

Con **Docker** stand-alone:

```bash
# Costruisci l'immagine
docker build -t vtt_backend .

# Avvia il container
docker run -p 5000:5000 --env-file .env vtt_backend
```

### Se vedi un errore simile

```bash
unable to get image 'postgres:16-alpine': permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

## Risolvi cosi

```bash
newgrp docker
#e poi riprova
docker compose up -d
```

## In casi estremi

```bash
sudo docker compose up -d
```

### Struttura del progetto

```text
Vtt_backend/
├── app.py              # Entry point Flask
├── models.py           # Modelli DB (SQLAlchemy)
├── routes/             # Endpoint API (Auth, Maps, Users)
├── ws/                 # Gestione WebSocket (Game Logic)
├── uploads/            # File caricati (Mappe/Immagini)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

### Note per lo sviluppo

- Database: I dati persistono nel volume Docker postgres_data.
- Env Variables: Mai committare il file .env. È in .gitignore.
- Migrazione: Da SQLite a Postgres completata. Usare psycopg2-binary
