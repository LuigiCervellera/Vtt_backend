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

- **Linux / macOS**: Docker & Docker Compose installati.
- **Windows**: [Docker Desktop](https://www.docker.com/products/docker-desktop/) installato e avviato (si raccomanda l'uso del backend WSL2).
- File `.env` configurato (vedi sotto).

### Avvio

Per avviare il backend è necessario utilizzare Docker.

Con **Docker Compose** (Consigliato, avvia sia il backend che il DB):

```bash
# Avvia DB e Backend (funziona su Linux, macOS e Windows in PowerShell/Terminal)
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

### Risoluzione Problemi Comune

#### 🐧 Su Linux: Errore di permessi socket

Se vedi un errore simile:

```bash

unable to get image 'postgres:16-alpine': permission denied while trying to connect to the docker API at unix:///var/run/docker.sock

```

Risolvi così:

```bash
newgrp docker
# e poi riprova
docker compose up -d

```

In casi estremi:

```bash
sudo docker compose up -d
```

### Se hai bisogno di riavviare il backend a seguito di modifiche al codice

```bash
docker compose down && docker compose up -d --build
```

#### 🪟 Su Windows: Errore di connessione al demone

Se in PowerShell/Prompt vedi un errore simile:

```text
error during connect: This error may indicate that the docker daemon is not running.

```

Risolvi così:

1. Assicurati che **Docker Desktop** sia aperto e attivo (l'icona della balena nella barra delle applicazioni deve essere verde/attiva).
2. Se usi WSL2, assicurati che l'integrazione con la tua distro sia attiva nelle impostazioni di Docker Desktop (`Settings -> Resources -> WSL integration`).
3. Riapri il terminale (PowerShell o CMD) come Amministratore e riprova.

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

---

## ☕ Supporta il Progetto

[![Buy me a coffee](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/K3K8NSM0V)
