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
```bash
# Avvia DB e Backend
docker compose up -d --build

# Vedi i logs
docker compose logs -f backend
